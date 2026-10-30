import json

import pytest

from extractor.dataset import load_sroie, parse_box_line, split_ids


def write_receipt(root, sample_id, key, box_lines):
    (root / "img" / f"{sample_id}.jpg").write_bytes(b"")
    (root / "key" / f"{sample_id}.json").write_text(json.dumps(key))
    (root / "box" / f"{sample_id}.csv").write_text("\n".join(box_lines) + "\n")


@pytest.fixture
def sroie_root(tmp_path):
    for sub in ("img", "key", "box"):
        (tmp_path / sub).mkdir()

    write_receipt(
        tmp_path,
        "000",
        {"company": "ABC SDN BHD", "date": "25/12/2018", "total": "9.00"},
        [
            "72,25,326,25,326,64,72,64,ABC SDN BHD",
            "110,144,383,144,383,163,110,163,NO.53, JALAN SAGU 18,",
        ],
    )

    return tmp_path


def test_parse_box_line_keeps_commas_in_text():
    line = parse_box_line("1,2,3,4,5,6,7,8,NO.53, JALAN SAGU 18,\r\n")

    assert line.box == (1, 2, 3, 4, 5, 6, 7, 8)
    assert line.text == "NO.53, JALAN SAGU 18,"


def test_parse_box_line_skips_malformed():
    assert parse_box_line("\n") is None


def test_load_sroie(sroie_root):
    [sample] = load_sroie(sroie_root, split="all")

    assert sample.id == "000"
    assert sample.image_path == sroie_root / "img" / "000.jpg"
    assert sample.receipt.company == "ABC SDN BHD"
    assert sample.receipt.address is None
    assert sample.text == "ABC SDN BHD\nNO.53, JALAN SAGU 18,"


def test_load_sroie_missing_root(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_sroie(tmp_path / "missing")


def test_split_ids_is_fixed_and_disjoint():
    ids = [f"{i:03d}" for i in range(626)]

    test = split_ids(ids, "test")
    dev = split_ids(ids, "dev")

    assert len(test) == 400
    assert len(dev) == 226
    assert set(test).isdisjoint(dev)
    assert split_ids(list(reversed(ids)), "test") == test


def test_split_ids_rejects_unknown_split():
    with pytest.raises(ValueError):
        split_ids(["000"], "train")
