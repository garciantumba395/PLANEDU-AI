import io
import unittest
import zipfile

from planedu import exports

COLS, ROWS = ["a", "b"], [["x", 1], ["y", 2]]


class WatermarkTest(unittest.TestCase):
    def test_pdf_has_logo_image_on_each_page(self):
        pdf = exports.pdf_bytes("T", COLS, ROWS * 400, "m")  # plusieurs pages
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreaterEqual(pdf.count(b"/Subtype /Image"), 1)
        self.assertGreaterEqual(pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page "), 2)

    def test_xlsx_contains_watermark_image(self):
        z = zipfile.ZipFile(io.BytesIO(exports.xlsx_bytes("T", COLS, ROWS, "m")))
        self.assertTrue(any(n.startswith("xl/media/") for n in z.namelist()))

    def test_csv_stays_pure_data(self):
        self.assertNotIn(b"PLANEDU", exports.csv_bytes(COLS, ROWS))


if __name__ == "__main__":
    unittest.main()
