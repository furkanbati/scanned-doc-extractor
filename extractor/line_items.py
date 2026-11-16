"""Rule-based line items: every purchased item with its amount, and the totals.

Works on OCR segments with their boxes (or on single words: the oracle mode
feeds the dataset's own words). No model, and nothing specific to a store:
each rule rests on a general property of receipts.

1. Geometry. The page's skew is the median slope of the wide boxes; boxes are
   rotated back and split into words, and words that overlap vertically form a
   row.
2. The amount column. Line amounts are right aligned, so the right edges of
   each row's rightmost money token cluster; the largest cluster is the column.
   A heading such as "PRICE" is not needed.
3. Row kinds. One keyword table (ROW_KEYWORDS) names subtotal, tax, service,
   discount, total, payment, change and item-count rows. The first such row
   with an amount ends the items; an unlabelled row equal to the sum of the
   items above it is a subtotal too.
4. Items. Every row in the item zone with an amount in the column is one item.
   A text row without an amount is the name of the item whose amount row
   below has no name, or else continues the item above (a long name or a
   modifier). Rows starting with "+" are modifiers, discounts in the item
   zone belong to the item above; neither is counted as an item.
5. Reconciliation. items = subtotal and subtotal + tax + service - discount
   = total, wherever the receipt prints those values. Anything that does not
   add up, and a missing total, sets needs_review.
"""

import math
import re
import statistics
from dataclasses import dataclass, field
from decimal import Decimal

from extractor.amounts import is_money, is_number, parse_amount
from extractor.ocr.base import OcrLine

# Row kinds and the words that mark them. "TOTAL QTY", discount and rounding
# lines are recognised first, because they often also contain TOTAL. Among
# the others the keyword that comes first on the line wins: "TOTAL BAYAR"
# (total to pay) is a total, "CASH" a payment, "SUB TOTAL" a subtotal. An
# item-count word alone ("ITEMS: 3") makes a count line only when nothing
# else matches ("ITEM: 3 SUBTOTAL 24,500" is a subtotal).
ROW_KEYWORDS: dict[str, str] = {
    "count": r"\b(?:QTY|QUANTITY|ITEMS?|ITEM\s*\(S\)|PCS|JML\s*ITEM)\b",
    "total_count": r"\bTOTAL\s*(?:QTY|QUANTITY|ITEMS?|ITEM\s*\(S\)|PCS)\b",
    "change": r"\b(?:CHANGE|KEMBALI\w*|KEMBALIAN)\b",
    "payment": (
        r"\b(?:CASH|TUNAI|CARD|CREDIT|KREDIT|DEBIT|PAYMENT|BAYAR|DIBAYAR|PAID|TENDER\w*|"
        r"EDC|E-?MONEY|WALLET|QRIS|VOUCHER|DEPOSIT)\b"
    ),
    "discount": r"\b(?:DISC\w*|DISKON|POTONGAN|PROMO\w*|HEMAT|SAVINGS?)\b",
    "rounding": r"\b(?:ROUNDING|PEMBULATAN|ROUND)\b",
    "subtotal": r"\bSUB\s*[-.]?\s*(?:TOTAL|TTL)\b|\bSUBTOTAL\b|\bSUBTTL\b",
    "tax": r"\b(?:TAX|P[B8]\s*-?\s*1|PPN|VAT|PAJAK|PBJT|RESTO\s*TAX)\b",
    "service": r"\b(?:SERVICE|SERVIS|SERV|SVC|S\s*/\s*C|S\.C\.?|SRV)\b",
    "total": r"\b(?:GRAND\s*TOTAL|TOTAL|TTL|JUMLAH|TAGIHAN|AMOUNT\s*DUE|NETT?O?|DUE)\b",
}
FIRST_KINDS = ("total_count", "discount", "rounding")
LEFTMOST_KINDS = ("subtotal", "tax", "service", "total", "payment", "change")
SUMMARY_KINDS = ("subtotal", "tax", "service", "total", "payment", "change", "rounding")
COMPILED = {kind: re.compile(pattern, re.IGNORECASE) for kind, pattern in ROW_KEYWORDS.items()}

WORD = re.compile(r"[A-Za-z]{2,}")
CURRENCY = re.compile(r"\b(?:RP|IDR)\b\.?", re.IGNORECASE)
QUANTITY = re.compile(r"^(?:[xX]\s*)?(\d{1,3})(?:[.,]0{1,2})?\s*[xX]?$")
# "15.000 x2", "0 x1", "2 x 15.000", "2 @ 15.000": a quantity and a unit price, nothing else.
UNIT_TIMES_QUANTITY = re.compile(r"^(?P<unit>\d[\d.,]*)\s*[xX]\s*(?P<quantity>\d{1,3})$")
QUANTITY_TIMES_UNIT = re.compile(r"^(?P<quantity>\d{1,3})\s*[xX@]\s*(?P<unit>\d[\d.,]*)$")
MAX_SKEW_DEGREES = 15


@dataclass(frozen=True)
class Token:
    text: str
    left: float
    right: float
    top: float
    bottom: float

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def middle(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Row:
    tokens: list[Token]

    @property
    def text(self) -> str:
        return " ".join(token.text for token in self.tokens)

    @property
    def top(self) -> float:
        return statistics.median(token.top for token in self.tokens)

    @property
    def bottom(self) -> float:
        return statistics.median(token.bottom for token in self.tokens)


@dataclass
class LineItem:
    name: str
    amount: Decimal
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    discount: Decimal = Decimal(0)
    modifiers: Decimal = Decimal(0)
    row: int = 0
    # The amount was computed from quantity x unit price, not read.
    derived: bool = False

    @property
    def quantity_checks(self) -> bool | None:
        """quantity x unit price = amount; None when either is not printed."""

        if self.quantity is None or self.unit_price is None:
            return None

        return self.quantity * self.unit_price == self.amount


@dataclass
class LineItemResult:
    items: list[LineItem] = field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    service: Decimal | None = None
    discount: Decimal | None = None
    rounding: Decimal | None = None
    total: Decimal | None = None
    cash: Decimal | None = None
    change: Decimal | None = None
    needs_review: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def amounts(self) -> list[Decimal]:
        return [item.amount for item in self.items]

    def to_json(self) -> dict:
        def number(value):
            return None if value is None else str(value)

        return {
            "items": [
                {
                    "name": item.name,
                    "amount": number(item.amount),
                    "quantity": number(item.quantity),
                    "unit_price": number(item.unit_price),
                }
                for item in self.items
            ],
            "subtotal": number(self.subtotal),
            "tax": number(self.tax),
            "service": number(self.service),
            "discount": number(self.discount),
            "total": number(self.total),
            "needs_review": self.needs_review,
            "reasons": self.reasons,
        }


# --- 1. Geometry ------------------------------------------------------------


def _points(box: tuple[int, ...]) -> list[tuple[float, float]]:
    return [(box[index], box[index + 1]) for index in range(0, 8, 2)]


def skew_angle(lines: list[OcrLine]) -> float:
    """Median slope of the top edge of boxes at least twice as wide as tall, in radians."""

    angles = []
    for line in lines:
        (x1, y1), (x2, y2), _, (x4, y4) = _points(line.box)
        width = math.hypot(x2 - x1, y2 - y1)
        height = math.hypot(x4 - x1, y4 - y1)
        if width >= 2 * height > 0:
            angles.append(math.atan2(y2 - y1, x2 - x1))

    if not angles:
        return 0.0

    angle = statistics.median(angles)

    return angle if abs(math.degrees(angle)) <= MAX_SKEW_DEGREES else 0.0


def to_tokens(lines: list[OcrLine], angle: float = 0.0) -> list[Token]:
    """Boxes rotated by -angle, each segment split into words.

    A word's horizontal extent is interpolated from its character offsets in
    the segment, so "2 NASI GORENG 30,000" in one OCR box yields four tokens.
    """

    cos, sin = math.cos(angle), math.sin(angle)
    tokens = []

    for line in lines:
        rotated = [(x * cos + y * sin, -x * sin + y * cos) for x, y in _points(line.box)]
        xs = [x for x, _ in rotated]
        ys = [y for _, y in rotated]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        text = line.text.strip()
        width = max(len(text), 1)

        for match in re.finditer(r"\S+", text):
            tokens.append(
                Token(
                    text=match.group(),
                    left=left + (right - left) * match.start() / width,
                    right=left + (right - left) * match.end() / width,
                    top=top,
                    bottom=bottom,
                )
            )

    return tokens


def _overlap(token: Token, other: Token) -> float:
    shared = min(token.bottom, other.bottom) - max(token.top, other.top)
    shortest = min(token.height, other.height)

    return shared / shortest if shortest > 0 else 0.0


def group_rows(tokens: list[Token], overlap: float = 0.5) -> list[Row]:
    """Words that share a printed line, top to bottom, each row left to right.

    A word joins the row whose horizontally nearest word overlaps it vertically
    by at least `overlap` of the shorter height. Comparing with the nearest
    word, not the row's first one, keeps a slightly curved line together.
    """

    rows: list[Row] = []

    for token in sorted(tokens, key=lambda token: token.middle):
        best, best_overlap = None, overlap

        for row in rows:
            nearest = min(
                row.tokens,
                key=lambda other: max(other.left - token.right, token.left - other.right, 0),
            )
            shared = _overlap(token, nearest)
            if shared >= best_overlap and not _collides(token, row):
                best, best_overlap = row, shared

        if best is None:
            rows.append(Row(tokens=[token]))
        else:
            best.tokens.append(token)

    for row in rows:
        row.tokens.sort(key=lambda token: token.left)
        row.tokens = join_split_numbers(row.tokens)

    return sorted(rows, key=lambda row: (row.top + row.bottom) / 2)


THOUSANDS_GROUP = re.compile(r"^\d{3}(?:[.,]\d{1,2})?$")
NUMBER_HEAD = re.compile(r"^(?:RP\.?)?-?\d{1,3}(?:[.,]\d{3})*[.,]?$", re.IGNORECASE)


def join_split_numbers(tokens: list[Token]) -> list[Token]:
    """Join a number OCR split at a thousands gap: "20" "000" -> "20000",
    "121," "768" -> "121,768". The second part must be one three-digit group
    starting within one line height of the first; a single digit opening the
    line is a quantity and stays apart ("1 500")."""

    joined: list[Token] = []

    for token in tokens:
        previous = joined[-1] if joined else None
        if (
            previous is not None
            and NUMBER_HEAD.match(previous.text)
            and THOUSANDS_GROUP.match(token.text)
            and token.left - previous.right <= previous.height
            and not (len(joined) == 1 and re.fullmatch(r"\d", previous.text))
        ):
            joined[-1] = Token(
                text=previous.text + token.text,
                left=previous.left,
                right=token.right,
                top=min(previous.top, token.top),
                bottom=max(previous.bottom, token.bottom),
            )
        else:
            joined.append(token)

    return joined


def _collides(token: Token, row: Row) -> bool:
    """A word cannot sit on top of another word of the same row."""

    width = token.right - token.left

    return any(
        min(token.right, other.right) - max(token.left, other.left) > 0.5 * min(width, other.right - other.left)
        for other in row.tokens
    )


# --- 2. The amount column ---------------------------------------------------


@dataclass(frozen=True)
class Column:
    right: float
    tolerance: float

    def holds(self, token: Token) -> bool:
        return abs(token.right - self.right) <= self.tolerance


def find_amount_column(rows: list[Row]) -> Column | None:
    """The right edge most rows' last money token lines up on."""

    heights = [token.height for row in rows for token in row.tokens if token.height > 0]
    if not heights:
        return None

    tolerance = 1.5 * statistics.median(heights)
    def edges_of(candidates: list[Row]) -> list[float]:
        return sorted(
            money[-1].right
            for row in candidates
            if (money := [token for token in row.tokens if _amount_like(token.text)])
        )

    # Each row's rightmost amount: a line total is printed right of the
    # quantity and unit price, whatever their format ("1.00 48000 48000").
    # Summary lines (TOTAL, CASH, CHANGE) are left out, or on a receipt with
    # one item they would outvote it when printed in a column of their own.
    edges = edges_of([row for row in rows if classify_row(row.text) is None]) or edges_of(rows)
    if not edges:
        return None

    clusters = [[edges[0]]]
    for edge in edges[1:]:
        if edge - clusters[-1][-1] <= tolerance:
            clusters[-1].append(edge)
        else:
            clusters.append([edge])

    # Most members; on a tie the rightmost column.
    best = max(clusters, key=lambda cluster: (len(cluster), statistics.median(cluster)))

    return Column(right=statistics.median(best), tolerance=tolerance)


def _signed(tokens: list[Token], index: int) -> Decimal | None:
    """The token's amount, negative when a lone '-' stands right before or after it."""

    value = parse_amount(tokens[index].text)
    if value is None:
        return None

    before = tokens[index - 1].text if index > 0 else ""
    after = tokens[index + 1].text if index + 1 < len(tokens) else ""
    if value > 0 and (before in ("-", "(-") or after in ("-", "-)")):
        value = -value

    return value


def _amount_like(text: str) -> bool:
    """Money, or a plain number of three digits or more ('15000'), never a bare count."""

    if is_money(text):
        return True

    return is_number(text) and len(re.sub(r"\D", "", text)) >= 3


def column_amount(row: Row, column: Column | None) -> tuple[int, Decimal] | None:
    """Index and value of the row's amount in the amount column.

    In the column, a short number counts too unless it is alone on the row:
    a free item is printed with amount "0".
    """

    if column is None:
        return None

    for index in range(len(row.tokens) - 1, -1, -1):
        token = row.tokens[index]
        fits = _amount_like(token.text) or (is_number(token.text) and index > 0)
        if fits and column.holds(token):
            value = _signed(row.tokens, index)
            if value is not None:
                return index, value

    return None


def rightmost_amount(row: Row) -> tuple[int, Decimal] | None:
    """Index and value of the row's last money token, wherever it is (large-print totals)."""

    for index in range(len(row.tokens) - 1, -1, -1):
        if _amount_like(row.tokens[index].text):
            value = _signed(row.tokens, index)
            if value is not None:
                return index, value

    return None


# --- 3. Row kinds -----------------------------------------------------------


def classify_row(text: str) -> str | None:
    """subtotal, tax, service, discount, rounding, total, payment, change, count, or None."""

    for kind in FIRST_KINDS:
        if COMPILED[kind].search(text):
            return "count" if kind == "total_count" else kind

    found = [
        (match.start(), kind)
        for kind in LEFTMOST_KINDS
        if (match := COMPILED[kind].search(text))
    ]
    if found:
        return min(found)[1]

    return "count" if COMPILED["count"].search(text) else None


def quantity_times_unit(text: str) -> tuple[Decimal, Decimal] | None:
    """(quantity, unit price) of a line that holds only those two."""

    text = CURRENCY.sub(" ", text).strip()
    match = UNIT_TIMES_QUANTITY.match(text) or QUANTITY_TIMES_UNIT.match(text)
    if not match:
        return None

    unit_price = parse_amount(match.group("unit"))
    if unit_price is None:
        return None

    return Decimal(match.group("quantity")), unit_price


def has_words(text: str) -> bool:
    """Letters other than a currency sign ("Rp. 25.000" has none)."""

    return bool(WORD.search(CURRENCY.sub(" ", text)))


# --- 4. Items ----------------------------------------------------------------


def _quantity_and_unit(tokens: list[Token]) -> tuple[Decimal | None, Decimal | None, list[Token]]:
    """Quantity and unit price among the tokens left of the amount; the rest is the name."""

    quantity = unit_price = None
    name = []

    for index, token in enumerate(tokens):
        text = token.text
        match = QUANTITY.match(text)
        if quantity is None and match and not is_money(text):
            following = tokens[index + 1].text if index + 1 < len(tokens) else ""
            # A number is a quantity at the start of the line, or next to an x / @.
            if not name or text.lower().endswith("x") or following.lower() in ("x", "@"):
                quantity = Decimal(match.group(1))
                continue

        if text.lower() in ("x", "@"):
            continue

        if unit_price is None and _amount_like(text):
            unit_price = parse_amount(text)
            continue

        name.append(token)

    return quantity, unit_price, name


@dataclass
class _ScannedRow:
    index: int
    row: Row
    kind: str | None
    amount: Decimal | None
    amount_index: int | None
    # The kind came from arithmetic, not from a keyword on the line.
    inferred: bool = False


def _scan(rows: list[Row], column: Column | None) -> list[_ScannedRow]:
    scanned = []
    taken = set()

    for index, row in enumerate(rows):
        if index in taken:
            continue

        kind = classify_row(row.text)
        found = column_amount(row, column)
        if found is None and kind is not None:
            found = rightmost_amount(row)

            # The amount printed on the line below its label.
            if found is None and index + 1 < len(rows):
                below = rows[index + 1]
                if not has_words(below.text) and classify_row(below.text) is None:
                    below_found = rightmost_amount(below)
                    if below_found is not None:
                        taken.add(index + 1)
                        row = Row(tokens=row.tokens + below.tokens)
                        found = (len(row.tokens) - len(below.tokens) + below_found[0], below_found[1])

        scanned.append(
            _ScannedRow(
                index=index,
                row=row,
                kind=kind,
                amount=found[1] if found else None,
                amount_index=found[0] if found else None,
            )
        )

    return scanned


def _item_zone_end(scanned: list[_ScannedRow]) -> int:
    """Position of the first summary row: a keyword row with an amount, or an
    unlabelled row that equals the sum of the amounts above it.

    A sum must be followed by summary lines, not by more items: an item can
    cost exactly what the items above it add up to. With a single item
    above, a row with the same amount could be a second item at the same
    price; it is a sum only when a payment or change line follows (a TOTAL
    line below would be the sum instead).
    """

    running, count = Decimal(0), 0
    amounts = [position for position, entry in enumerate(scanned) if entry.amount is not None]

    for order, position in enumerate(amounts):
        entry = scanned[position]

        if entry.kind in SUMMARY_KINDS:
            return position

        if entry.kind is None and count >= 1 and entry.amount == running:
            following = scanned[amounts[order + 1]] if order + 1 < len(amounts) else None
            if following is None or following.kind in ("payment", "change") or (
                count >= 2 and following.kind in SUMMARY_KINDS
            ):
                entry.kind, entry.inferred = "subtotal", True
                return position

        if entry.kind is None:
            running += entry.amount
            count += 1

    return len(scanned)


def _item_margin(scanned: list[_ScannedRow]) -> tuple[float, float] | None:
    """Where item names start (the median left edge of named amount rows) and
    how far right a row must start to count as indented (one line height)."""

    rows = [
        entry.row
        for entry in scanned
        if entry.amount is not None and entry.kind is None and has_words(entry.row.text)
    ]
    if len(rows) < 2:
        return None

    left = statistics.median(row.tokens[0].left for row in rows)
    height = statistics.median(token.height for row in rows for token in row.tokens)

    return left, height


def is_indented(row: Row, margin: tuple[float, float] | None) -> bool:
    """A named row that starts a line height or more right of the item names: a modifier."""

    if margin is None or not WORD.match(row.tokens[0].text):
        return False

    left, height = margin

    return row.tokens[0].left - left >= height


def _leading_quantity(row: Row) -> bool:
    first = row.tokens[0].text

    return bool(QUANTITY.match(first)) and not is_money(first)


def _quantity_style(scanned: list[_ScannedRow]) -> bool:
    """Most item lines open with a quantity ("1 NASI GORENG 25,000")."""

    named = [
        entry.row
        for entry in scanned
        if entry.amount is not None and entry.amount >= 0 and entry.kind is None and has_words(entry.row.text)
    ]
    with_quantity = sum(_leading_quantity(row) for row in named)

    return with_quantity >= 2 and with_quantity >= 0.75 * len(named)


def build_items(scanned: list[_ScannedRow]) -> list[LineItem]:
    """Items of the item zone. A priced line is a modifier of the item above
    (folded into it, not counted) when it opens with "+", is indented, or
    has no quantity where the item lines have one ("PEARL (L) 4,000" under
    "1 x MANGO GT (L) 25,000")."""

    items: list[LineItem] = []
    pending: list[str] = []
    margin = _item_margin(scanned)
    quantity_style = _quantity_style(scanned)

    for entry in scanned:
        tokens = entry.row.tokens
        text = entry.row.text

        if entry.amount is None:
            if has_words(text) and entry.kind is None:
                pending.append(text)
            elif pending and entry.kind is None and (derived := quantity_times_unit(text)):
                # "0 x1" under a name, its amount not printed (or not read).
                quantity, unit_price = derived
                items.append(
                    LineItem(
                        name=pending.pop(),
                        amount=quantity * unit_price,
                        quantity=quantity,
                        unit_price=unit_price,
                        row=entry.index,
                        derived=True,
                    )
                )
            continue

        # A discount line, or any negative amount, reduces the item above it.
        if entry.kind == "discount" or entry.amount < 0:
            if items:
                items[-1].discount += -abs(entry.amount)
            continue

        if entry.kind is not None:
            continue

        no_quantity = quantity_style and not pending and has_words(text) and not _leading_quantity(entry.row)
        if tokens[0].text.startswith("+") or (items and (is_indented(entry.row, margin) or no_quantity)):
            if items:
                items[-1].modifiers += entry.amount
            continue

        quantity, unit_price, name_tokens = _quantity_and_unit(tokens[: entry.amount_index])
        name = " ".join(token.text for token in name_tokens if not re.fullmatch(r"[-:.]+", token.text))

        if not has_words(name) and pending:
            if items and len(pending) > 1:
                items[-1].name = " ".join([items[-1].name, *pending[:-1]]).strip()
            name = pending[-1]
        elif pending and items:
            items[-1].name = " ".join([items[-1].name, *pending]).strip()
        pending = []

        items.append(
            LineItem(
                name=name,
                amount=entry.amount,
                quantity=quantity,
                unit_price=unit_price,
                row=entry.index,
            )
        )

    return items


# --- 5. Totals and reconciliation ------------------------------------------

# Totals are often rounded (55,834 printed, 55,800 paid): sums that differ by
# at most this share of the total still add up.
ROUNDING_SHARE = Decimal("0.002")
ADJUSTMENT_KINDS = ("tax", "service", "rounding", "discount")


def _trailing_zeros(value: Decimal) -> int:
    digits = str(abs(value.normalize()).quantize(Decimal(1))) if value == value.to_integral() else "1"

    return len(digits) - len(digits.rstrip("0")) if digits != "0" else 0


def adds_up(found: Decimal, expected: Decimal) -> bool:
    """Equal, or rounded: close to the sum and rounder than it (55,834 -> 55,800).

    A misread digit (121,798 read as 121,768) is close but not rounder, so it
    does not add up.
    """

    if found == expected:
        return True

    close = abs(found - expected) <= ROUNDING_SHARE * max(abs(found), abs(expected))

    return close and _trailing_zeros(found) > _trailing_zeros(expected)


@dataclass
class _Summary:
    subtotal: Decimal | None
    tax: Decimal | None
    service: Decimal | None
    discount: Decimal | None
    rounding: Decimal | None

    def expected_total(self, items_sum: Decimal) -> Decimal:
        base = self.subtotal if self.subtotal is not None else items_sum
        added = sum((v for v in (self.tax, self.service, self.rounding) if v is not None), Decimal(0))

        return base + added - abs(self.discount or 0)


def _adjustments(entries: list[_ScannedRow], items_sum: Decimal) -> _Summary:
    """Subtotal and adjustments printed in these summary rows.

    A tax or service line whose amount is as large as the subtotal is the
    taxable base ("Dasar Pengenaan Pajak"), not the tax; a rounding line
    larger than 1% of it is a misread total.
    """

    subtotals = [e.amount for e in entries if e.kind == "subtotal"]
    subtotal = subtotals[-1] if subtotals else None
    base = abs(subtotal if subtotal is not None else items_sum) or Decimal(1)

    values: dict[str, list[Decimal]] = {kind: [] for kind in ADJUSTMENT_KINDS}
    for entry in entries:
        if entry.kind not in values:
            continue
        if entry.kind in ("tax", "service") and abs(entry.amount) >= base:
            continue
        if entry.kind == "rounding" and abs(entry.amount) > base / 100:
            continue
        values[entry.kind].append(entry.amount)

    def total_of(kind: str) -> Decimal | None:
        return sum(values[kind], Decimal(0)) if values[kind] else None

    return _Summary(
        subtotal=subtotal,
        tax=total_of("tax"),
        service=total_of("service"),
        discount=total_of("discount"),
        rounding=total_of("rounding"),
    )


def _paid(entries: list[_ScannedRow]) -> Decimal | None:
    """cash - change, when both are printed."""

    cash = next((e.amount for e in entries if e.kind == "payment"), None)
    change = next((e.amount for e in reversed(entries) if e.kind == "change"), None)

    return cash - abs(change) if cash is not None and change is not None else None


def summarize_rows(entries: list[_ScannedRow], items_sum: Decimal) -> tuple[_Summary, Decimal | None, str]:
    """The totals below the items, the total, and how the total was found.

    Each TOTAL line is checked against the subtotal and adjustments printed
    above it (a tax line below the total is already included in it). The
    TOTAL line that equals cash - change wins, then the last one that adds
    up, then the last one. Without a TOTAL line amount, an unlabelled amount
    that adds up is taken, and reported as such.
    """

    entries = [e for e in entries if e.amount is not None]
    for entry in entries:
        # A percentage line ("P.Rest 10 %", "PB1 10%") adjusts the total.
        if entry.kind is None and "%" in entry.row.text:
            entry.kind = "tax"

    paid = _paid(entries)
    totals = [position for position, e in enumerate(entries) if e.kind == "total"]

    def expected_at(position: int) -> tuple[_Summary, Decimal]:
        summary = _adjustments(entries[:position], items_sum)
        return summary, summary.expected_total(items_sum)

    chosen, found_by = next((p for p in totals if entries[p].amount == paid), None), "line"
    if chosen is None:
        fitting = [p for p in totals if adds_up(entries[p].amount, expected_at(p)[1])]
        if fitting:
            chosen = fitting[-1]
            # A later TOTAL line that does not add up: the one chosen may be
            # a pre-tax total. Keep it, but a person checks.
            if chosen != totals[-1]:
                found_by = "superseded"

    if chosen is not None:
        summary = expected_at(chosen)[0]
        total = entries[chosen].amount

        # Tax or service printed below the TOTAL line (before payment): that
        # line was a subtotal when a later line prints it plus them.
        below = []
        for entry in entries[chosen + 1:]:
            if entry.kind in ("payment", "change"):
                break
            if entry.kind in ("tax", "service"):
                below.append(entry.amount)
        if below:
            target = total + sum(below, Decimal(0))
            later = [e.amount for e in entries[chosen + 1:] if e.kind in (None, "total", "payment")]
            if target in later or target == paid:
                summary = _adjustments(entries, items_sum)
                summary.subtotal = total
                return summary, target, "arithmetic"
            found_by = "superseded"

        return summary, total, found_by

    # No TOTAL line adds up (none printed, or its amount misread: "000.800").
    # An unlabelled or payment line printing the expected sum is the total;
    # failing that, the last TOTAL line, which reconciliation will flag.
    summary = _adjustments(entries, items_sum)
    expected = summary.expected_total(items_sum)
    if paid == expected or any(
        (e.kind in (None, "payment", "total") or e.inferred) and e.amount == expected
        for e in entries
    ):
        return summary, expected, "arithmetic"

    if totals:
        return expected_at(totals[-1])[0], entries[totals[-1]].amount, "line"

    return summary, None, "none"


def reconcile(result: LineItemResult) -> None:
    reasons = []
    items_sum = sum((item.amount + item.discount + item.modifiers for item in result.items), Decimal(0))
    plain_sum = sum((item.amount for item in result.items), Decimal(0))

    if not result.items:
        reasons.append("no items")

    if result.subtotal is not None and result.subtotal not in (items_sum, plain_sum):
        reasons.append(f"items add up to {items_sum}, subtotal is {result.subtotal}")

    summary = _Summary(result.subtotal, result.tax, result.service, result.discount, result.rounding)
    expected = summary.expected_total(items_sum)

    if result.total is None:
        reasons.append("no total")
    elif not adds_up(result.total, expected):
        reasons.append(f"expected total {expected}, found {result.total}")

    if any(item.amount == 0 for item in result.items):
        # Free lines are items on some receipts and modifiers on others;
        # the sums cannot tell, so a person decides.
        reasons.append("a free (0) line may be an item or a modifier")

    if any(item.derived for item in result.items):
        reasons.append("an amount was computed from quantity x unit price, not read")

    if any(item.modifiers for item in result.items):
        reasons.append("modifier lines were folded into the item above")

    for item in result.items:
        if item.quantity_checks is False:
            reasons.append(f"{item.quantity} x {item.unit_price} is not {item.amount}")

    result.reasons = reasons
    result.needs_review = bool(reasons)


def extract_line_items(lines: list[OcrLine]) -> LineItemResult:
    """Items, totals and needs_review from OCR segments or words with boxes."""

    if not lines:
        return LineItemResult(reasons=["no text"])

    rows = group_rows(to_tokens(lines, skew_angle(lines)))
    column = find_amount_column(rows)
    scanned = _scan(rows, column)
    end = _item_zone_end(scanned)

    items = build_items(scanned[:end])
    items_sum = sum((item.amount + item.discount + item.modifiers for item in items), Decimal(0))
    summary, total, found_by = summarize_rows(scanned[end:], items_sum)

    result = LineItemResult(
        items=items,
        subtotal=summary.subtotal,
        tax=summary.tax,
        service=summary.service,
        discount=summary.discount,
        rounding=summary.rounding,
        total=total,
    )

    reconcile(result)
    if found_by == "arithmetic":
        result.reasons.append("total found by arithmetic, not by a TOTAL line")
        result.needs_review = True
    elif found_by == "superseded":
        result.reasons.append("lines below the TOTAL line (a later total, tax) do not add up with it")
        result.needs_review = True

    return result
