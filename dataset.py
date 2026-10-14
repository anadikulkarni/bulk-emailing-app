"""Reading recipient lists out of uploaded CSV / Excel files.

Pure data helpers (no Streamlit) so the parsing rules are easy to reason about:
load any CSV/Excel into a table of strings, find the email column(s), pull and
validate the addresses, and filter rows by the values in other columns.
"""
import csv
import io
import re
from collections import Counter

import pandas as pd

EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls")
FILE_TYPES = ["csv", "tsv", "txt", "xlsx", "xlsm", "xls"]

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}"
)
_EMAIL_HEADER_RE = re.compile(r"e[\s_-]*mail|mail[\s_-]*id", re.I)
_NAME_HEADER_RE = re.compile(
    r"name|customer|distributor|company|firm|party|retailer|shop|store|business", re.I
)
# Location-style columns, broadest first, used as the default filters.
_LOCATION_WORDS = ["zone", "region", "state", "area", "district", "city", "town",
                   "territory", "location", "branch"]
_CELL_SPLIT_RE = re.compile(r"[;,|\n\r]+")
_TOKEN_STRIP = " \t'\"<>()[]"


# ---------- reading files ----------
def _decode(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def _sniff_delimiter(text):
    sample = text.splitlines()[:50]
    best, best_score = ",", (0, 0)
    for delim in [",", ";", "\t", "|"]:
        counts = [len(r) for r in csv.reader(sample, delimiter=delim) if r]
        if not counts:
            continue
        width, hits = Counter(counts).most_common(1)[0]
        if width < 2:
            continue
        score = (hits / len(counts), width)
        if score > best_score:
            best, best_score = delim, score
    return best


def _clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return " ".join(str(value).split())


def _read_rows(data, filename, sheet=None):
    if filename.lower().endswith(EXCEL_EXTENSIONS):
        raw = pd.read_excel(io.BytesIO(data), sheet_name=sheet or 0, header=None, dtype=str)
        return [[_clean(v) for v in row] for row in raw.itertuples(index=False)]
    text = _decode(data)
    delim = "\t" if filename.lower().endswith(".tsv") else _sniff_delimiter(text)
    return [[_clean(v) for v in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def sheet_names(data, filename):
    """Sheet names for an Excel file, [] for CSV."""
    if not filename.lower().endswith(EXCEL_EXTENSIONS):
        return []
    return pd.ExcelFile(io.BytesIO(data)).sheet_names


def detect_header_row(rows, scan=10):
    """Index of the header row, or None if the file has no header row.

    The header is the first of the top rows that sits above the first row with
    an email address and is at least half as full as that row — which skips
    title lines above the real header. With no email in sight, it is the first
    row (nearly) as full as the fullest one.
    """
    head = rows[:50]
    filled = [sum(1 for c in r if c) for r in head]
    if not any(filled):
        return None
    first_email = next(
        (i for i, r in enumerate(head) if any(parse_email_cell(c)[0] for c in r)), None
    )
    if first_email is None:
        widest = max(filled[:scan])
        return next((i for i, n in enumerate(filled[:scan]) if n and n >= 0.8 * widest), None)
    for i in range(min(first_email, scan)):
        if filled[i] and filled[i] >= 0.5 * filled[first_email]:
            return i
    return None


def read_table(data, filename, sheet=None, header_row="auto"):
    """Load a CSV/Excel file into a DataFrame of stripped strings.

    header_row: "auto", None (no header row) or a 0-based row index.
    The index holds the spreadsheet row number of each record, so problems can
    be traced back to the file.
    """
    rows = _read_rows(data, filename, sheet)
    if header_row == "auto":
        header_row = detect_header_row(rows)
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]

    if header_row is None:
        header, start = [""] * width, 0
    else:
        header, start = rows[header_row], header_row + 1

    columns, seen = [], Counter()
    for i, h in enumerate(header):
        h = h or f"Column {i + 1}"
        seen[h] += 1
        columns.append(h if seen[h] == 1 else f"{h} ({seen[h]})")

    body = rows[start:]
    df = pd.DataFrame(body, columns=columns, index=range(start + 1, start + 1 + len(body)))
    df = df[(df != "").any(axis=1)]
    # drop columns with neither a header nor any data
    keep = [c for c, h in zip(columns, header) if h or (df[c] != "").any()]
    return df[keep]


def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


# ---------- emails ----------
def is_valid_email(value):
    return bool(_EMAIL_RE.fullmatch(value))


def parse_email_cell(value):
    """Split one cell into (valid_addresses, invalid_fragments).

    Handles several addresses in one cell (comma / semicolon / newline / pipe
    separated) and "Name <a@b.com>" or "mailto:" forms.
    """
    valid, invalid = [], []
    for token in _CELL_SPLIT_RE.split(value or ""):
        bracketed = re.fullmatch(r"[^<>@]*<([^<>]+)>\s*", token)
        token = (bracketed.group(1) if bracketed else token).strip(_TOKEN_STRIP).rstrip(".")
        if token.lower().startswith("mailto:"):
            token = token[7:]
        if not token:
            continue
        if is_valid_email(token):
            valid.append(token)
            continue
        # "a@x.com b@y.com" — but never salvage part of "first last@x.com",
        # which would mail a different (possibly real) address.
        parts = [p.strip(_TOKEN_STRIP) for p in re.split(r"[\s/]+", token)]
        parts = [p for p in parts if p]
        if len(parts) > 1 and all(is_valid_email(p) for p in parts):
            valid.extend(parts)
        else:
            invalid.append(token)
    return valid, invalid


# Typo targets — long enough that a 1–2 letter slip can't be another real domain.
_COMMON_DOMAINS = [
    "gmail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "rediffmail.com", "hotmail.com",
    "outlook.com", "icloud.com", "rocketmail.com", "googlemail.com", "hotmail.co.in",
    "outlook.in", "yahoo.co.uk", "hotmail.co.uk", "protonmail.com",
]
_KNOWN_DOMAINS = set(_COMMON_DOMAINS) | {
    "ymail.com", "live.com", "live.in", "mail.com", "aol.com", "msn.com", "me.com",
    "mac.com", "gmx.com", "zoho.com", "zohomail.in", "proton.me",
}


def _edit_distance(a, b):
    """Damerau-Levenshtein distance (a swap of neighbours is one edit)."""
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def suggest_domain_fix(email):
    """The address with its domain corrected when it looks like a misspelt
    common provider (gmai.com, gamil.com, gmail.con …), else None."""
    local, _, domain = email.rpartition("@")
    domain = domain.lower()
    if domain in _KNOWN_DOMAINS:
        return None
    for known in _COMMON_DOMAINS:  # "gmail.comakash"
        if domain.startswith(known):
            return f"{local}@{known}"
    best = min(_COMMON_DOMAINS, key=lambda k: _edit_distance(domain, k))
    allowed = 2 if len(domain) >= 8 else 1  # short domains: one slip at most
    return f"{local}@{best}" if _edit_distance(domain, best) <= allowed else None


def detect_email_columns(df):
    """Columns that look like they hold email addresses, best first."""
    scored = []
    for col in df.columns:
        values = df[col][df[col] != ""].head(500)
        at_share = values.str.contains("@", regex=False).mean() if len(values) else 0.0
        named = bool(_EMAIL_HEADER_RE.search(col))
        if at_share >= 0.3 or (named and at_share > 0):
            scored.append((named, at_share, col))
    if not scored:
        scored = [(True, 0.0, c) for c in df.columns if _EMAIL_HEADER_RE.search(c)]
    return [c for _, _, c in sorted(scored, key=lambda s: (s[0], s[1]), reverse=True)]


def detect_name_column(df, exclude=()):
    candidates = [c for c in df.columns if c not in exclude and _NAME_HEADER_RE.search(c)]
    exact = [c for c in candidates if c.strip().lower() == "name"]
    return (exact or candidates or [None])[0]


def detect_location_columns(df, exclude=()):
    """Area / city style columns, broadest first — default filters for a list."""
    found = []
    for col in df.columns:
        if col in exclude:
            continue
        lowered = col.lower()
        ranks = [i for i, w in enumerate(_LOCATION_WORDS) if w in lowered]
        if ranks:
            found.append((min(ranks), col))
    return [c for _, c in sorted(found)]


def extract_recipients(df, email_cols, name_col=None, detail_cols=()):
    """Pull unique, valid addresses out of df, with an accounting of every row.

    Returns a dict with:
      recipients     [{email, name, detail, rows}] unique by address (case-insensitive)
      rows           total rows looked at
      rows_valid     rows with at least one valid address
      rows_blank     rows where every email column is empty
      rows_invalid   rows with something in the email column but no valid address
      addresses      valid addresses found, duplicates included
      duplicates     addresses dropped because they were already in the list
      problems       [{row, name, value, issue}] rows that are blank or hold invalid text
    """
    found = {}
    rows_valid = rows_blank = rows_invalid = addresses = 0
    problems = []
    names = df[name_col].tolist() if name_col else [""] * len(df)
    email_values = [df[c].tolist() for c in email_cols]
    detail_values = [df[c].tolist() for c in detail_cols]
    for i, row_no in enumerate(df.index):
        cells = [col[i] for col in email_values if col[i]]
        if not cells:
            rows_blank += 1
            problems.append({"row": row_no, "name": names[i], "value": "", "issue": "no email"})
            continue
        valid, invalid = [], []
        for cell in cells:
            v, bad = parse_email_cell(cell)
            valid += v
            invalid += bad
        if invalid:
            problems.append({"row": row_no, "name": names[i], "value": " | ".join(cells),
                             "issue": "partly invalid" if valid else "invalid email"})
        if not valid:
            rows_invalid += 1
            continue
        rows_valid += 1
        detail = " / ".join(col[i] for col in detail_values if col[i])
        for email in valid:
            addresses += 1
            key = email.lower()
            if key in found:
                found[key]["rows"] += 1
            else:
                found[key] = {"email": email, "name": names[i], "detail": detail, "rows": 1}
    return {
        "recipients": list(found.values()),
        "rows": len(df),
        "rows_valid": rows_valid,
        "rows_blank": rows_blank,
        "rows_invalid": rows_invalid,
        "addresses": addresses,
        "duplicates": addresses - len(found),
        "problems": problems,
    }


def parse_typed_emails(raw):
    """Addresses typed into a text box: (unique valid in order, rejected entries)."""
    valid, rejected, seen = [], [], set()
    for line in (raw or "").splitlines():
        v, bad = parse_email_cell(line)
        rejected += bad
        for e in v:
            if e.lower() not in seen:
                seen.add(e.lower())
                valid.append(e)
    return valid, rejected


# ---------- filters ----------
def norm(value):
    """Comparison form of a cell: whitespace collapsed, case-folded."""
    return " ".join(str(value).split()).casefold()


def value_options(series):
    """(keys sorted for display, {key: display text}, {key: row count}) for a column."""
    keys = series.map(norm)
    counts = keys.value_counts().to_dict()
    display = {}
    for key, original in zip(keys, series):
        display.setdefault(key, original)
    ordered = sorted(counts, key=lambda k: (k == "", k))
    return ordered, display, counts


def apply_filters(df, value_filters=None, text_filters=None):
    """Rows matching every filter.

    value_filters: {column: [normalised values]}  — row value must be one of them
    text_filters:  {column: "term, term"}          — row value must contain any term
    Empty filters are ignored.
    """
    mask = pd.Series(True, index=df.index)
    for col, keys in (value_filters or {}).items():
        if keys:
            mask &= df[col].map(norm).isin(set(keys))
    for col, text in (text_filters or {}).items():
        terms = [norm(t) for t in str(text).split(",") if norm(t)]
        if terms:
            normed = df[col].map(norm)
            mask &= normed.map(lambda v: any(t in v for t in terms))
    return df[mask]
