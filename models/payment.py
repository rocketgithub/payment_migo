# coding: utf-8
import logging
import urllib
import random
import hmac
import hashlib
import base64
import uuid

from odoo import api, fields, models, _
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.addons.payment_migo.controllers.payment import MigoController
from odoo.tools.float_utils import float_compare
from odoo.release import version_info
from odoo.http import request

import requests

_logger = logging.getLogger(__name__)

class AcquirerMigo(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('migo', 'Migo')], ondelete={'migo': 'set default'})
    migo_public_key = fields.Char('Public Key', required_if_provider='migo', groups='base.group_user')
    migo_private_key = fields.Char('Private Key', required_if_provider='migo', groups='base.group_user')
    migo_client = fields.Char('Client', required_if_provider='migo', groups='base.group_user')

    def migo_form_generate_values(self, values):
        session_id = request.session.sid
        token = hashlib.sha256('{}:{}'.format(self.migo_private_key, self.migo_public_key).encode('utf-8')).hexdigest()
        _logger.warning(token)

        data = {
            'amount': values['amount'],
            'userId': values['reference'],
            'channel': 'wa',
            'client': self.migo_client,
            'createdBy': 'MigoTest',
            'ads': [],
            'customKeys': { 'session_id': session_id }
        }
        _logger.warning(data)
        
        r = requests.post('https://sb-mw.migopayments.com/transactions', json=data, headers={'Authorization': token})
        resultado = r.json()
        _logger.warning(resultado)
        
        base_url = self.env['ir.config_parameter'].get_param('web.base.url')
        migo_tx_values = dict(values)
        migo_tx_values.update({
            'return_url': urllib.parse.urljoin(base_url, MigoController._return_url),
            'migo_order_id': resultado['uid'],
        })
        return migo_tx_values

    def migo_get_form_action_url(self):
        self.ensure_one()
        if ( 'environment' in self.fields_get() and self.environment == 'prod' ) or ( 'state' in self.fields_get() and self.state == 'enabled' ):
            return "https://sandbox.migopayments.com/"
        else:
            return "https://sandbox.migopayments.com/"

class TxMigo(models.Model):
    _inherit = 'payment.transaction'

    @api.model
    def _migo_form_get_tx_from_data(self, data):
        """
        Given a data dict coming from migo, verify it and find the related
        transaction record.
        """
        reference = data.get('req_reference_number')
        if not reference:
            error_msg = _('Migo: received data with missing reference (%s)') % (reference)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        tx = self.search([('reference', '=', reference)])
        _logger.info(tx)

        if version_info[0] == 11:
            if tx.sale_order_id:
                data['return_url'] = '/quote/%d/%s' % (tx.sale_order_id.id, tx.sale_order_id.access_token)
            else:
                data['return_url'] = '/my/invoices/%d?access_token=%s' % (tx.account_invoice_id.id, tx.account_invoice_id.access_token)

        if not tx or len(tx) > 1:
            error_msg = _('Migo: received data for reference %s') % (reference)
            if not tx:
                error_msg += _('; no order found')
            else:
                error_msg += _('; multiple order found')
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        return tx

    def _migo_form_get_invalid_parameters(self, data):
        invalid_parameters = []
        status_code = data.get('decision', 'ERROR')
                    
        if status_code == 'ACCEPT':
            if float_compare(float(data.get('auth_amount', '0.0')), self.amount, 2) != 0:
                invalid_parameters.append(('auth_amount', data.get('auth_amount'), '%.2f' % self.amount))
                
        return invalid_parameters

    def _migo_form_validate(self, data):
        status_code = data.get('decision', 'ERROR')
        vals = {
            "acquirer_reference": data.get('transaction_id'),
        }
        if status_code == 'ACCEPT':
            if version_info[0] > 11:
                self.write(vals)
                self._set_transaction_done()
            else:
                vals['state'] = 'done'
                vals['date'] = fields.Datetime.now()
                self.write(vals)
            return True
        else:
            error = 'Migo: error '+data.get('message')
            _logger.info(error)
            if version_info[0] > 11:
                self.write(vals)
                self._set_transaction_error(error)
            else:
                vals['state'] = 'error'
                vals['state_message'] = error
                vals['date'] = fields.Datetime.now()
                self.write(vals)
            return False