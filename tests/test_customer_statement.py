from datetime import date
from unittest.mock import MagicMock

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
        self.assertEqual(summary["subtotal_cargos"], 300.0)
        self.assertEqual(summary["saldo_corte"], 275.0)

    def test_summary_matches_july_comparison(self):
        summary = self.Wizard._compute_summary(
            7018.40,
            3239.50,
            4218.00,
            10728.40,
        )
        self.assertAlmostEqual(summary["subtotal_cargos"], 7457.50, places=2)
        self.assertAlmostEqual(summary["saldo_corte"], 3747.50, places=2)

    def test_previous_period_dates_cross_year_boundary(self):
        date_from, date_to = self.Wizard._previous_period_dates(date(2026, 1, 1))
        self.assertEqual(date_from, date(2025, 12, 1))
        self.assertEqual(date_to, date(2025, 12, 31))

    def test_journal_flag_controls_invoice_category(self):
        self.assertIn(
            "mayan_otros_cargos",
            self.env["account.journal"]._fields,
        )

        purchase = MagicMock()
        purchase.move_type = "out_invoice"
        purchase.journal_id.mayan_otros_cargos = False
        self.assertEqual(
            self.Wizard._statement_move_category(purchase),
            "purchase",
        )

        other_charge = MagicMock()
        other_charge.move_type = "out_invoice"
        other_charge.journal_id.mayan_otros_cargos = True
        self.assertEqual(
            self.Wizard._statement_move_category(other_charge),
            "other_charge",
        )

        credit_note = MagicMock()
        credit_note.move_type = "out_refund"
        self.assertEqual(
            self.Wizard._statement_move_category(credit_note),
            "credit_note",
        )

    def test_receivable_entry_is_classified_by_balance_and_journal(self):
        purchase = MagicMock()
        purchase.balance = 26.0
        purchase.move_id.journal_id.mayan_otros_cargos = False
        self.assertEqual(
            self.Wizard._statement_receivable_category(purchase),
            "purchase",
        )

        other_charge = MagicMock()
        other_charge.balance = 26.0
        other_charge.move_id.journal_id.mayan_otros_cargos = True
        self.assertEqual(
            self.Wizard._statement_receivable_category(other_charge),
            "other_charge",
        )

        credit = MagicMock()
        credit.balance = -26.0
        self.assertEqual(
            self.Wizard._statement_receivable_category(credit),
            "credit",
        )

    def test_direct_receivable_entry_rolls_into_next_opening(self):
        wizard = self.Wizard.new({"company_id": self.env.company.id})
        adjustment = MagicMock()
        adjustment.date = date(2025, 4, 15)
        adjustment.balance = 26.0
        adjustment.move_id.journal_id.mayan_otros_cargos = False

        period = wizard._period_activity(
            MagicMock(),
            False,
            date(2025, 10, 31),
            include_lines=False,
            moves=[],
            payments=[],
            receivable_adjustments=[adjustment],
        )
        self.assertAlmostEqual(period["purchases"], 26.0, places=2)

        summary = wizard._compute_summary(
            5428.80,
            period["purchases"],
            period["other_charges"],
            period["payments"],
        )
        self.assertAlmostEqual(summary["saldo_corte"], 5454.80, places=2)

    def test_report_text_is_safe_ascii_html(self):
        report = self.env[
            "report.mayan_customer_statement.customer_statement_document"
        ]
        rendered = str(report._html_text("Jos\u00e9 & <Club>"))
        self.assertEqual(rendered, "Jos&#233; &amp; &lt;Club&gt;")
        self.assertTrue(rendered.isascii())
