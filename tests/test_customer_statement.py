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

    def test_partner_selector_only_allows_members(self):
        self.assertEqual(
            self.Wizard._fields["partner_ids"].domain,
            [("es_socio", "=", True)],
        )

    def test_partner_selector_uses_member_code_ordered_list(self):
        partner_list = self.env.ref(
            "mayan_customer_statement.view_mayan_customer_statement_partner_list"
        )
        wizard_form = self.env.ref(
            "mayan_customer_statement.view_mayan_customer_statement_wizard_form"
        )

        self.assertIn(
            'default_order="codigo_socio asc, name asc"',
            partner_list.arch,
        )
        self.assertIn("list_view_ref", wizard_form.arch)
        self.assertNotIn("tree_view_ref", wizard_form.arch)

    def test_statement_partners_are_sorted_by_numeric_member_code(self):
        partner_1914 = MagicMock(codigo_socio=1914, name="Alberto", id=10)
        partner_27 = MagicMock(codigo_socio=27, name="Beatriz", id=20)
        partner_305 = MagicMock(codigo_socio=305, name="Carlos", id=30)

        partners = sorted(
            [partner_1914, partner_27, partner_305],
            key=self.Wizard._partner_statement_sort_key,
        )

        self.assertEqual(
            [partner.codigo_socio for partner in partners],
            [27, 305, 1914],
        )

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

    def test_payment_amount_uses_receivable_credit_when_signed_amount_is_zero(self):
        payment = MagicMock()
        payment.company_id.currency_id.is_zero.side_effect = (
            lambda amount: abs(amount) < 0.001
        )
        payment.move_id.line_ids.filtered.return_value.mapped.return_value = [-137.50]
        payment.amount_company_currency_signed = 0.0

        amount = self.Wizard._payment_company_amount(payment)

        self.assertAlmostEqual(amount, 137.50, places=2)
        payment.currency_id._convert.assert_not_called()

    def test_payment_amount_falls_back_to_signed_company_amount(self):
        payment = MagicMock()
        payment.company_id.currency_id.is_zero.side_effect = (
            lambda amount: abs(amount) < 0.001
        )
        payment.move_id.line_ids.filtered.return_value.mapped.return_value = []
        payment.amount_company_currency_signed = 85.25

        amount = self.Wizard._payment_company_amount(payment)

        self.assertAlmostEqual(amount, 85.25, places=2)
        payment.currency_id._convert.assert_not_called()

    def test_report_text_is_safe_ascii_html(self):
        report = self.env[
            "report.mayan_customer_statement.customer_statement_document"
        ]
        rendered = str(report._html_text("Jos\u00e9 & <Club>"))
        self.assertEqual(rendered, "Jos&#233; &amp; &lt;Club&gt;")
        self.assertTrue(rendered.isascii())

    def test_report_uses_article_wrapper_for_utf8_pdf_rendering(self):
        report_view = self.env.ref(
            "mayan_customer_statement.customer_statement_document"
        )

        self.assertIn('<div class="article">', report_view.arch)
        self.assertIn("Cargos del Mes", report_view.arch)

    def test_statement_recipient_uses_partner_email(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Socio con correo",
                "email": "socio@example.com",
            }
        )
        self.assertEqual(
            self.Wizard._statement_recipient(partner),
            "socio@example.com",
        )

    def test_statement_filename_is_safe_and_identifies_period(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Socio / Prueba",
                "ref": "COD/123",
            }
        )
        wizard = self.Wizard.create(
            {
                "company_id": self.env.company.id,
                "partner_ids": [(6, 0, [partner.id])],
                "year": 2025,
                "month": "11",
            }
        )
        filename = wizard._statement_filename(partner)
        self.assertNotIn("/", filename)
        self.assertTrue(filename.endswith("_2025_11.pdf"))

    def test_email_history_matches_selected_partner_and_period(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Socio historial",
                "email": "historial@example.com",
            }
        )
        log = self.env["mayan.customer.statement.email.log"].create(
            {
                "partner_id": partner.id,
                "company_id": self.env.company.id,
                "year": 2025,
                "month": "11",
                "recipient": partner.email,
                "state": "sent",
                "user_id": self.env.user.id,
            }
        )
        wizard = self.Wizard.create(
            {
                "company_id": self.env.company.id,
                "partner_ids": [(6, 0, [partner.id])],
                "year": 2025,
                "month": "11",
            }
        )
        self.assertEqual(wizard.email_log_ids, log)
        self.assertEqual(wizard.email_sent_count, 1)
        self.assertEqual(wizard.email_queued_count, 0)
        self.assertEqual(wizard.email_failed_count, 0)

    def test_sent_mail_is_recorded_as_sent(self):
        mail = MagicMock()
        mail.state = "sent"
        values = self.Wizard._email_log_result_values(mail)
        self.assertEqual(values["state"], "sent")
        self.assertTrue(values["sent_at"])
        self.assertFalse(values["failure_reason"])
