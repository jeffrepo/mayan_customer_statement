from html import escape

from markupsafe import Markup
from odoo import api, models
from odoo.tools.misc import formatLang


REPORT_LABELS = {
    "code": "C\u00d3DIGO:",
    "description": "Descripci\u00f3n",
    "debits": "D\u00e9bitos",
    "credits": "Cr\u00e9ditos",
    "footer": (
        "Si realiz\u00f3 un pago que no aparece en este estado de cuenta, "
        "comun\u00edquese con el \u00e1rea de Contabilidad, correo: "
        "cuentacorriente@mayangolfclub.com y al n\u00famero de tel\u00e9fono: "
        "66855800, Ext. 5806"
    ),
    "legal_note": (
        "Los socios tienen obligaci\u00f3n de cubrir mensualmente sus deudas a "
        "favor de la Tesorer\u00eda del club. Si sus cuentas no fueren cubiertas "
        "dentro del mes siguiente, la Tesorer\u00eda suspender\u00e1 el cr\u00e9dito "
        "del socio moroso, notific\u00e1ndole por carta."
    ),
}


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
            "html_text": self._html_text,
            "labels": {
                key: self._html_text(value) for key, value in REPORT_LABELS.items()
            },
        }

    @api.model
    def _format_amount(self, amount, currency):
        formatted = formatLang(self.env, amount, currency_obj=currency)
        # wkhtmltopdf deployments with a legacy content-type can render the
        # non-breaking space from formatLang as "Â ". A regular space keeps the
        # amount readable without changing its numeric or currency formatting.
        formatted = formatted.replace("\u00a0", " ").replace("\u202f", " ")
        return self._html_text(formatted)

    @api.model
    def _html_text(self, value):
        """Return escaped HTML containing ASCII bytes only.

        Some legacy wkhtmltopdf proxies decode the rendered UTF-8 response as
        Latin-1. Numeric HTML entities survive that incorrect decoding while
        the browser still renders the intended accented characters.
        """
        escaped = escape("" if value is None else str(value), quote=True)
        ascii_html = escaped.encode("ascii", "xmlcharrefreplace").decode("ascii")
        return Markup(ascii_html)
