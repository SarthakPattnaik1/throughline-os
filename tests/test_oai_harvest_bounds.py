"""Bound OAI harvests even when a repository returns no usable records."""
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock
from throughline_connectors.oai import OAIRepository, OAIError, OAI
from throughline_connectors.base import ConnectorError

def page(count=0, token="", deleted=False):
    root = ET.Element(OAI + "OAI-PMH")
    listing = ET.SubElement(root, OAI + "ListRecords")
    for i in range(count):
        record = ET.SubElement(listing, OAI + "record")
        header = ET.SubElement(record, OAI + "header")
        if deleted:
            header.set("status", "deleted")
        ET.SubElement(header, OAI + "identifier").text = str(i)
    ET.SubElement(listing, OAI + "resumptionToken").text = token
    return root

class HarvestBoundsTests(unittest.TestCase):
    def repo(self, pages):
        repo = object.__new__(OAIRepository)
        repo._verb = Mock(side_effect=pages)
        repo._record = Mock(return_value={"title": "Synthetic"})
        return repo

    def test_invalid_limits_do_not_fetch(self):
        for field in ("max_records", "max_pages"):
            for value in (0, -1, True, 1.5, None):
                repo = self.repo([])
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ConnectorError):
                        repo.harvest(**{field: value})
                    repo._verb.assert_not_called()

    def test_deleted_records_count_toward_limit(self):
        result = self.repo([page(3, deleted=True)]).harvest(max_records=2)
        self.assertEqual(result["deleted"], ["0", "1"])
        self.assertTrue(result["truncated"])

    def test_unusable_records_count_toward_limit(self):
        repo = self.repo([page(3)])
        repo._record.return_value = None
        result = repo.harvest(max_records=2)
        self.assertEqual(repo._record.call_count, 2)
        self.assertTrue(result["truncated"])

    def test_exact_last_page_is_not_truncated(self):
        self.assertFalse(self.repo([page(2)]).harvest(max_records=2)["truncated"])

    def test_empty_pages_are_bounded(self):
        result = self.repo([page(token="one"), page(token="two")]).harvest(max_pages=2)
        self.assertEqual(result["pages"], 2)
        self.assertTrue(result["truncated"])

    def test_repeated_token_refused(self):
        with self.assertRaises(ConnectorError):
            self.repo([page(token="same"), page(token="same")]).harvest()

    def test_token_is_preserved_and_filters_not_repeated(self):
        repo = self.repo([page(token="  opaque  token  "), page()])
        repo.harvest(since="2026-01-01")
        repo._verb.assert_called_with("ListRecords", resumptionToken="  opaque  token  ")

    def test_missing_listing_is_not_empty_success(self):
        with self.assertRaises(ConnectorError):
            self.repo([ET.Element(OAI + "OAI-PMH")]).harvest()

    def test_later_no_records_does_not_erase_deletions(self):
        with self.assertRaises(OAIError):
            self.repo([page(1, token="next", deleted=True),
                       OAIError("invalid continuation", code="noRecordsMatch")]).harvest()

    def test_initial_no_records_is_empty_success(self):
        result = self.repo([OAIError("empty", code="noRecordsMatch")]).harvest()
        self.assertEqual(result["records"], [])
        self.assertFalse(result["truncated"])
