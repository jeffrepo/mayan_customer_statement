from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    mayan_otros_cargos = fields.Boolean(
        string="Otros cargos",
        help=(
            "Las facturas de cliente publicadas en este diario se mostrarán "
            "en Otros cargos. Las facturas de diarios sin marcar se mostrarán "
            "en Compras y cadis."
        ),
    )
