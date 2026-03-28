"""Tests for metadata extraction."""

from smartcat.parsing.metadata import extract_entities


class TestMonetaryExtraction:
    def test_dollar_amount(self):
        entities = extract_entities("The deal is worth $1,500.00 total.")
        monetary = [e for e in entities if e.entity_type == "monetary"]
        assert len(monetary) >= 1
        assert any("1,500" in e.entity_value for e in monetary)

    def test_dollar_million(self):
        entities = extract_entities("Revenue was $2.5 million last quarter.")
        monetary = [e for e in entities if e.entity_type == "monetary"]
        assert len(monetary) >= 1

    def test_mmbtu(self):
        entities = extract_entities("Delivery of 10,000 MMBtu at the hub.")
        monetary = [e for e in entities if e.entity_type == "monetary"]
        assert len(monetary) >= 1
        assert any("MMBtu" in e.entity_value for e in monetary)

    def test_no_false_positives(self):
        entities = extract_entities("Hello, how are you doing today?")
        monetary = [e for e in entities if e.entity_type == "monetary"]
        assert len(monetary) == 0


class TestDateExtraction:
    def test_mm_dd_yyyy(self):
        entities = extract_entities("Meeting scheduled for 01/15/2001.")
        dates = [e for e in entities if e.entity_type == "date_ref"]
        assert len(dates) >= 1
        assert any("01/15/2001" in e.entity_value for e in dates)

    def test_month_dd_yyyy(self):
        entities = extract_entities("Due by January 15, 2001.")
        dates = [e for e in entities if e.entity_type == "date_ref"]
        assert len(dates) >= 1

    def test_abbreviated_month(self):
        entities = extract_entities("Filed on Dec 4, 2001.")
        dates = [e for e in entities if e.entity_type == "date_ref"]
        assert len(dates) >= 1


class TestDocumentRefExtraction:
    def test_file_tag(self):
        entities = extract_entities("See << File: report.xls >> for details.")
        docs = [e for e in entities if e.entity_type == "document_ref"]
        assert any("report.xls" in e.entity_value for e in docs)

    def test_attached_file(self):
        entities = extract_entities("Attached: quarterly_report.pdf")
        docs = [e for e in entities if e.entity_type == "document_ref"]
        assert any("quarterly_report.pdf" in e.entity_value for e in docs)

    def test_standalone_filename(self):
        entities = extract_entities("Please review data-export.xlsx before the meeting.")
        docs = [e for e in entities if e.entity_type == "document_ref"]
        assert any("data-export.xlsx" in e.entity_value for e in docs)


class TestDealIdExtraction:
    def test_deal_number(self):
        entities = extract_entities("Regarding Deal #45678, we need to adjust terms.")
        deals = [e for e in entities if e.entity_type == "deal_id"]
        assert len(deals) >= 1
        assert any("45678" in e.entity_value for e in deals)

    def test_contract_ref(self):
        entities = extract_entities("Per Contract #ABC-123, the terms are as follows.")
        deals = [e for e in entities if e.entity_type == "deal_id"]
        assert len(deals) >= 1

    def test_docket(self):
        entities = extract_entities("FERC Docket No. ER01-1234.")
        deals = [e for e in entities if e.entity_type == "deal_id"]
        assert len(deals) >= 1


class TestContextExtraction:
    def test_context_included(self):
        text = "Some prefix text. The total was $5,000 for the project. And some suffix."
        entities = extract_entities(text)
        monetary = [e for e in entities if e.entity_type == "monetary"]
        assert len(monetary) >= 1
        assert "$5,000" in monetary[0].context

    def test_deduplication(self):
        text = "$1,000 was mentioned. Then $1,000 again."
        entities = extract_entities(text)
        monetary = [e for e in entities if e.entity_type == "monetary"]
        assert len(monetary) == 1  # Deduped
