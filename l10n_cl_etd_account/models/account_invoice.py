# Copyright (C) 2019 Konos
# Copyright (C) 2019 Blanco Martín & Asociados
# Copyright (C) 2019 CubicERP
# Copyright (C) 2019 Open Source Integrators
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, models, fields, _

import logging

_logger = logging.getLogger(__name__)

try:
    from io import BytesIO
except:
    _logger.warning("no se ha cargado io")

try:
    import pdf417gen
except ImportError:
    _logger.warning('Cannot import pdf417gen library')
try:
    import base64
except ImportError:
    _logger.warning('Cannot import base64 library')



SII_MAPPING = {
    # class_id.code: class_id.code of the refund
    30: 60,  # Factura -> Nota de Crédito
    33: 61,  # Factura Electrónica -> Nota de Crédito Electrónica
    35: 60,  # Boleta -> Nota de Crédito
    39: 61,  # Boleta Electrónica -> Nota de Crédito Electrónica
    60: 55,  # Nota de Crédito -> Nota de Débito
    61: 56,  # Nota de Crédito Electrónica -> Nota de Débito Electrónica
}


class AccountInvoice(models.Model):
    _name = 'account.invoice'
    _inherit = ['account.invoice', 'etd.mixin']

    medio_pago = fields.Selection([
            ("CH", "Cheque"),
            ("CF", "Cheque a fecha"),
            ("LT", "letra"),
            ("EF", "Efectivo"),
            ("PE", "Pago A Cta. Cte."),
            ("TC", "Tarjeta Crédito"),
            ("OT", "Otro")
        ],
        string="Medio Pago",
    )

    forma_pago = fields.Selection(
            [
                    ('1', 'Contado'),
                    ('2', 'Crédito'),
                    ('3', 'Gratuito')
            ],
            string="Forma de pago",
            default='1'
        )

    def pdf417bc(self, ted, columns=13, ratio=3):
        bc = pdf417gen.encode(
            ted,
            security_level=5,
            columns=columns,
        )
        image = pdf417gen.render_image(
            bc,
            padding=15,
            scale=1,
            ratio=ratio,
        )
        return image

    @api.multi
    def get_barcode_img(self, columns=13, ratio=3):
        barcodefile = BytesIO()
        image = self.pdf417bc(self.sii_barcode, columns, ratio)
        image.save(barcodefile, 'PNG')
        data = barcodefile.getvalue()
        return base64.b64encode(data)

    def _get_barcode_img(self):
        for r in self:
            if r.sii_barcode:
                r.sii_barcode_img = r.get_barcode_img()

    sii_barcode = fields.Char(
            copy=False,
            string=_('SII Barcode'),
            help='SII Barcode Name',
            readonly=True,
        )
    sii_barcode_img = fields.Binary(
            string=_('SII Barcode Image'),
            help='SII Barcode Image in PDF417 format',
            compute="_get_barcode_img",
        )

    def _compute_class_id_domain(self):
        return [('document_type', 'in', ('invoice', 'invoice_in',
                                         'debit_note', 'credit_note'))]

    def get_etd_document(self):
        res = super().get_etd_document()
        # res = res.filtered(
        #     lambda x: x.invoicing_policy == self.partner_id.invoicing_policy
        #     or not x.invoicing_policy)
        return res

    @api.multi
    def invoice_validate(self):
        res = super().invoice_validate()
        sign = self._name in [x.model for x in self.company_id.etd_ids]
        for invoice in self:
            if sign and invoice.type in ('out_invoice', 'out_refund'):
                self.with_delay().document_sign()
        return res

    def get_reverse_sii_code(self, code=False):
        return code and SII_MAPPING.get(code, False) or False

    def get_reverse_sii_document(self):
        sii_code = self.get_reverse_sii_code(code=self.class_id.code)
        return self.env['sii.document.class'].search([
            ('code', '=', sii_code)
        ], limit=1)

    @api.multi
    @api.returns('self')
    def refund(self, date_invoice=None, date=None, description=None,
               journal_id=None):
        refunds = super().refund(
            date_invoice=date_invoice, date=date, description=description,
            journal_id=journal_id)
        for index, invoice in enumerate(self):
            sii_doc = invoice.get_reverse_sii_document()
            refunds[index].class_id = sii_doc.id or False
        return refunds

    @api.model
    def create(self, vals):
        if not vals.get('class_id', False):
            # Default: Factura Electrónica
            sii_code = 33
            if vals.get('type', False) == 'out_invoice':
                # Get partner
                partner = self.env['res.partner'].browse(
                    vals.get('partner_id', False))
                if partner.invoicing_policy == 'ticket':
                    # Boleta Electrónica
                    sii_code = 39
            elif vals.get('type', False) == 'out_refund':
                # Nota de crédito Electrónica
                sii_code = 61
            if sii_code:
                vals.update({
                    'class_id': self.env['sii.document.class'].search([
                        ('code', '=', sii_code)
                    ], limit=1).id or False
                })
        return super().create(vals)

    @api.depends('tax_line_ids')
    def compute_sii_document_class(self):
        for rec in self:
            if not rec.tax_line_ids:
                # Boleta Electrónica (39) -> Boleta Electrónica Exenta (41)
                if rec.class_id and rec.class_id.code == 39:
                    rec.class_id = self.env['sii.document.class'].search([
                        ('code', '=', 41)
                    ], limit=1).id or False
                # Factura Electrónica (33) -> Factura Electrónica Exenta (34)
                if rec.class_id and rec.class_id.code == 33:
                    rec.class_id = self.env['sii.document.class'].search([
                        ('code', '=', 34)
                    ], limit=1).id or False
