from odoo import _, fields, models
from odoo.exceptions import UserError


MONTH_SELECTION = [
    ("1", "Enero"),
    ("2", "Febrero"),
    ("3", "Marzo"),
    ("4", "Abril"),
    ("5", "Mayo"),
    ("6", "Junio"),
    ("7", "Julio"),
    ("8", "Agosto"),
    ("9", "Septiembre"),
    ("10", "Octubre"),
    ("11", "Noviembre"),
    ("12", "Diciembre"),
]


class MayanCustomerStatementEmailLog(models.Model):
    _name = "mayan.customer.statement.email.log"
    _description = "Historial de envíos de estados de cuenta"
    _order = "create_date desc, id desc"

    partner_id = fields.Many2one(
        "res.partner",
        string="Socio",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    year = fields.Integer(string="Año", required=True, readonly=True, index=True)
    month = fields.Selection(
        MONTH_SELECTION,
        string="Mes",
        required=True,
        readonly=True,
        index=True,
    )
    recipient = fields.Char(
        string="Destinatario",
        required=True,
        readonly=True,
    )
    subject = fields.Char(string="Asunto", readonly=True)
    state = fields.Selection(
        [
            ("queued", "En cola"),
            ("sent", "Enviado"),
            ("failed", "Error"),
        ],
        string="Estado",
        required=True,
        readonly=True,
        default="queued",
        index=True,
    )
    sent_at = fields.Datetime(string="Enviado el", readonly=True)
    failure_reason = fields.Text(string="Detalle", readonly=True)
    mail_id = fields.Many2one(
        "mail.mail",
        string="Correo de Odoo",
        readonly=True,
        ondelete="set null",
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="PDF",
        readonly=True,
        ondelete="set null",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Enviado por",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
    )

    def action_download_attachment(self):
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_("Este intento de envío no tiene un PDF adjunto."))
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % self.attachment_id.id,
            "target": "self",
        }
