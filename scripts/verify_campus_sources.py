"""Verify the shipped excerpts, optionally against the original local DOCX files."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from researchos.adapters.campus import manifest, source_from_document
from researchos.services.campus_report import comparison_quote


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-root", type=Path)
    args = parser.parse_args()
    documents = manifest()["documents"]
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraph_count = 0
    for document in documents:
        source = source_from_document(document, "q1")
        for field in (
            "招生对象",
            "名额",
            "选拔规则",
            "推免依据",
            "毕业学分",
            "标准学制",
            "修业年限",
            "学位",
        ):
            comparison_quote(source, field)
        paragraph_count += len(document["paragraphs"])
        if args.knowledge_root:
            path = args.knowledge_root / document["source_path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != document["source_sha256"]:
                raise SystemExit(f"Source hash differs: {document['id']}")
            with zipfile.ZipFile(path) as archive:
                tree = ET.fromstring(archive.read("word/document.xml"))
            paragraphs = [
                "".join(text.text or "" for text in node.findall(".//w:t", namespace)).strip()
                for node in tree.findall(".//w:p", namespace)
            ]
            for paragraph in document["paragraphs"]:
                if paragraphs[paragraph["index"] - 1] != paragraph["text"]:
                    raise SystemExit(f"Source paragraph differs: {document['id']}")
    print(
        json.dumps(
            {
                "documents": len(documents),
                "paragraphs": paragraph_count,
                "packaged_excerpts": "verified",
                "original_docx": "verified" if args.knowledge_root else "not checked",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
