"""OAI XML guards must hold across encodings and long prologs."""
import unittest

from throughline_connectors.base import ConnectorError
from throughline_connectors.oai import _parse


class OAIXMLSafetyTests(unittest.TestCase):
    def test_plain_xml(self):
        self.assertEqual(_parse(b"<OAI-PMH/>").tag, "OAI-PMH")

    def test_utf16_plain_xml(self):
        self.assertEqual(_parse("<OAI-PMH/>".encode("utf-16")).tag, "OAI-PMH")

    def test_declarations_are_refused(self):
        declaration = '<!DOCTYPE root [<!ENTITY example "expanded">]>'
        for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
            for padding in ("", "<!--" + "x" * 4096 + "-->"):
                with self.subTest(encoding=encoding, padding=len(padding)):
                    raw = (padding + declaration + "<root>&example;</root>").encode(encoding)
                    with self.assertRaises(ConnectorError):
                        _parse(raw)

    def test_external_doctype_is_refused(self):
        with self.assertRaises(ConnectorError):
            _parse(b'<!DOCTYPE root SYSTEM "file:///not-to-be-read"><root/>')

    def test_malformed_xml_is_connector_error(self):
        with self.assertRaises(ConnectorError):
            _parse(b"<root>")

if __name__ == "__main__":
    unittest.main()
