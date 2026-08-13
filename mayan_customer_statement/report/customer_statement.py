from odoo import api, models
from odoo.tools.misc import formatLang


class MayanCustomerStatementReport(models.AbstractModel):
    _name = "report.mayan_customer_statement.customer_statement_document"
    _description = "Reporte de estado de cuenta de clientes Mayan"

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.env["mayan.customer.statement.wizard"].browse(docids)
        wizard.ensure_one()
        return {
            "doc_ids": wizard.ids,
            "doc_model": wizard._name,
            "docs": wizard,
            "statements": wizard._build_statements(),
            "format_amount": self._format_amount,
        }

    @api.model
    def _format_amount(self, amount, currency):
        return formatLang(self.env, amount, currency_obj=currency)
