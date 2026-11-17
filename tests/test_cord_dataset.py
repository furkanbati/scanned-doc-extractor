from decimal import Decimal

from evaluation.cord import parse_truth, parse_words, quad_box


def test_parse_truth_list_menu():
    truth = parse_truth(
        {
            "menu": [
                {"nm": "REAL GANACHE", "cnt": "1", "price": "16,500"},
                {"nm": "EGG TART", "cnt": "1", "price": "13.000", "sub": {"nm": "+Hot"}},
            ],
            "sub_total": {"subtotal_price": "29,500", "tax_price": "2,950"},
            "total": {"total_price": "32,450", "cashprice": "50,000"},
        }
    )

    assert truth.item_count == 2
    assert truth.amounts == [Decimal(16500), Decimal(13000)]
    assert truth.subtotal == Decimal(29500)
    assert truth.tax == Decimal(2950)
    assert truth.total == Decimal(32450)
    assert truth.service is None


def test_parse_truth_single_item_as_dict():
    truth = parse_truth({"menu": {"nm": "TEA", "price": "5.000"}, "total": {"total_price": "5.000"}})

    assert truth.item_count == 1
    assert truth.amounts == [Decimal(5000)]


def test_repeated_value_keeps_the_last():
    truth = parse_truth({"menu": [], "total": {"total_price": ["55,834", "55,800"]}})

    assert truth.total == Decimal(55800)


def test_sub_total_given_as_list_is_merged():
    truth = parse_truth({"menu": [], "sub_total": [{"subtotal_price": "10,000"}, {"tax_price": "1,000"}]})

    assert truth.subtotal == Decimal(10000)
    assert truth.tax == Decimal(1000)


def test_missing_sections():
    truth = parse_truth({"menu": [{"nm": "X"}]})

    assert truth.total is None
    assert truth.amounts == [None]


def test_quad_box_order():
    quad = {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "x3": 5, "y3": 6, "x4": 7, "y4": 8}

    assert quad_box(quad) == (1, 2, 3, 4, 5, 6, 7, 8)


def test_parse_words_drops_labels_and_blanks():
    quad = {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "x3": 10, "y3": 5, "x4": 0, "y4": 5}
    words = parse_words(
        [
            {
                "category": "menu.nm",
                "group_id": 3,
                "words": [
                    {"text": "TEA", "quad": quad, "row_id": 1, "is_key": 0},
                    {"text": " ", "quad": quad, "row_id": 1, "is_key": 0},
                ],
            }
        ]
    )

    assert [word.text for word in words] == ["TEA"]
    assert words[0].box == (0, 0, 10, 0, 10, 5, 0, 5)
