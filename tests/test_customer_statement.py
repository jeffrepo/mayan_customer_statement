from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMayanCustomerStatement(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wizard = cls.env["mayan.customer.statement.wizard"]

    def test_default_period_is_previous_month(self):
        today = fields.Date.context_today(self.Wizard)
        expected = today.replace(day=1) - relativedelta(months=1)
        defaults = self.Wizard.default_get(["year", "month"])
        self.assertEqual(defaults["year"], expected.year)
        self.assertEqual(defaults["month"], str(expected.month))

    def test_summary_formula(self):
        summary = self.Wizard._compute_summary(100.0, 250.0, 50.0, 125.0)
        self.assertEqual(summary["subtotal_cargos"], 400.0)
        self.assertEqual(summary["saldo_corte"], 275.0)

    def test_report_text_is_safe_ascii_html(self):
        report = self.env[
            "report.mayan_customer_statement.customer_statement_document"
        ]
        rendered = str(report._html_text("Jos\u00e9 & <Club>"))
        self.assertEqual(rendered, "Jos&#233; &amp; &lt;Club&gt;")
        self.assertTrue(rendered.isascii())
