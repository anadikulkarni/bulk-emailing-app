"""Streamlit pieces for choosing recipients.

recipient_picker  — the select / deselect list shared by every recipient mode
dataset_picker    — recipients from a table: email column, column filters, counts
file_picker       — dataset_picker for an uploaded CSV / Excel file
keep, keep_alive  — keep widget choices while a widget is off screen
"""
import hashlib
import re

import pandas as pd
import streamlit as st

import dataset

PAGE_SIZE = 100
# columns with more distinct values than this get a "contains" box, not a list
MAX_FILTER_OPTIONS = 2000

HEADER_CHOICES = ["Auto-detect", "No header row"] + [f"Row {i}" for i in range(1, 11)]
_KEPT = "_kept_widget_keys"


# ---------- keeping widget state ----------
def keep(key):
    """Register a widget key whose value must survive reruns where the widget
    isn't drawn (another recipient mode or page, a filter that hides it).
    Streamlit otherwise forgets it. Returns the key."""
    st.session_state.setdefault(_KEPT, set()).add(key)
    return key


def keep_alive():
    """Call once per run, before any widget is drawn."""
    for key in list(st.session_state.get(_KEPT, ())):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def _first_time(key, **defaults):
    """Widget defaults, only for the run that creates the widget (Streamlit
    warns when a default meets a value already in session state)."""
    return {} if key in st.session_state else defaults


@st.cache_data(show_spinner=False, max_entries=20)
def load_table(data, filename, sheet=None, header_row="auto"):
    return dataset.read_table(data, filename, sheet, header_row)


@st.cache_data(show_spinner=False, max_entries=20)
def _sheet_names(data, filename):
    return dataset.sheet_names(data, filename)


def file_token(data):
    return hashlib.md5(data).hexdigest()[:12]


def _md_escape(text):
    return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~$])", r"\\\1", text)


def _clear_checkboxes(ns, keys=None):
    prefix = f"{ns}_chk_"
    for key in [k for k in st.session_state.keys() if str(k).startswith(prefix)]:
        if keys is None or key[len(prefix):] in keys:
            del st.session_state[key]


# ---------- shared select / deselect list ----------
def recipient_picker(recipients, ns, reset_token):
    """Let the user tick / untick individual recipients (all ticked to start).

    recipients: [{"email", "name", "detail"?, "key"?, "fixed_from"?}] with
    unique emails. Ticks are remembered per key (default: the lower-cased
    address) until reset_token changes — a stable key keeps an untick when the
    shown address changes, e.g. after a typo correction.
    Returns the ticked addresses, in list order.
    """
    sel_key, page_key = f"{ns}_selections", f"{ns}_page"
    search_key, last_search_key = f"{ns}_search", f"{ns}_last_search"
    prefix = f"{ns}_chk_"

    if sel_key not in st.session_state or st.session_state.get(f"{ns}_token") != reset_token:
        st.session_state[f"{ns}_token"] = reset_token
        st.session_state[sel_key] = {}
        st.session_state[page_key] = 0
        st.session_state[last_search_key] = None
        st.session_state[search_key] = ""  # also clears the box in the browser
        _clear_checkboxes(ns)

    def rkey(r):
        return r.get("key") or r["email"].lower()

    selections = st.session_state[sel_key]
    for r in recipients:
        # a checkbox clicked on the last run holds the newest value
        if prefix + rkey(r) in st.session_state:
            selections[rkey(r)] = st.session_state[prefix + rkey(r)]
        selections.setdefault(rkey(r), True)

    total = len(recipients)
    selected_count = sum(1 for r in recipients if selections[rkey(r)])
    st.info(f"{selected_count} of {total} recipients selected.")

    with st.expander("Preview & select recipients"):
        search = st.session_state.get(search_key, "").strip().lower()
        filtered = [
            r for r in recipients
            if search in r["name"].lower()
            or search in r["email"].lower()
            or search in r.get("detail", "").lower()
        ] if search else recipients

        # select all / deselect all (just the search matches while searching)
        col_a, col_b, _ = st.columns([1, 1, 4])
        for col, label, value in [(col_a, "Select all", True), (col_b, "Deselect all", False)]:
            if col.button(label, key=f"{ns}_{'sel' if value else 'desel'}_all"):
                for r in filtered:
                    selections[rkey(r)] = value
                _clear_checkboxes(ns, {rkey(r) for r in filtered})
                st.rerun()

        st.text_input("Search by name or email", key=search_key)
        if search:
            st.caption(f"Select all / Deselect all apply to the {len(filtered)} matches.")

        # reset to page 0 when search or the list changes
        list_sig = hashlib.md5("\n".join(rkey(r) for r in recipients).encode()).hexdigest()
        if (search, list_sig) != st.session_state[last_search_key]:
            st.session_state[page_key] = 0
            st.session_state[last_search_key] = (search, list_sig)

        total_pages = max(1, -(-len(filtered) // PAGE_SIZE))
        st.session_state[page_key] = min(st.session_state[page_key], total_pages - 1)
        page_idx = st.session_state[page_key]
        page_slice = filtered[page_idx * PAGE_SIZE: (page_idx + 1) * PAGE_SIZE]

        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        if pcol1.button("← Prev", key=f"{ns}_pg_prev", disabled=page_idx == 0):
            st.session_state[page_key] -= 1
            st.rerun()
        pcol2.markdown(
            f"<div style='text-align:center; padding-top:6px'>"
            f"Page {page_idx + 1} of {total_pages} "
            f"({len(filtered)} result{'s' if len(filtered) != 1 else ''})"
            f"</div>",
            unsafe_allow_html=True,
        )
        if pcol3.button("Next →", key=f"{ns}_pg_next", disabled=page_idx >= total_pages - 1):
            st.session_state[page_key] += 1
            st.rerun()

        # checkboxes for this page only; their state seeds from `selections`
        for r in page_slice:
            key = prefix + rkey(r)
            if key not in st.session_state:
                st.session_state[key] = selections[rkey(r)]
            label = f"{r['name']}  —  {r['email']}" if r["name"] else r["email"]
            if r.get("fixed_from"):
                label += f"  ✏️ corrected from {r['fixed_from']}"
            if r.get("detail"):
                label += f"  ·  {r['detail']}"
            st.checkbox(_md_escape(label), key=key)

    return [r["email"] for r in recipients if selections[rkey(r)]]


# ---------- recipients from a table ----------
def _value_filter(df, base, col, key):
    """Multiselect of a column's values, limited to the rows left in `base`.

    Labels come from the whole column so they are identical on every run: the
    browser hands back the label text, and a label that changed (say, a count
    in it) would no longer match and the choice would silently drop out.
    """
    _, display, _ = dataset.value_options(df[col])
    keys, _, counts = dataset.value_options(base[col])
    # keep earlier choices listed (even if nothing matches now) so they can be removed
    keys += [k for k in st.session_state.get(key, []) if k not in counts]
    return st.multiselect(
        col, keys, key=keep(key), placeholder="All",
        format_func=lambda k: display.get(k) or "(blank)",
    )


def _problem_rows(problems, title, hint):
    if not problems:
        return
    with st.expander(title):
        st.dataframe(
            pd.DataFrame(problems).rename(columns={
                "row": "Row in file", "name": "Name", "value": "Email cell", "issue": "Issue",
            }),
            hide_index=True, width="stretch",
        )
        st.caption(hint)


def _corrections(full, result, t):
    """Offer corrections for addresses that are probably typos — a misspelt
    domain (gmai.com) or stray spaces (gmail. com) — and, if the user opts in,
    use the corrected addresses. Returns the recipient list to use.

    Suggestions come from the whole file, so the opt-in stays on screen (and
    on) while filters change. Corrected recipients keep a stable key, so anyone
    already unticked stays unticked.
    """
    fixes = {}
    for r in full["recipients"]:
        fix = dataset.suggest_domain_fix(r["email"])
        if fix:
            fixes[r["key"]] = fix
    recipients = result["recipients"]
    if not fixes and not full["fixable"]:
        return recipients
    rows = [{"Name": r["name"], "In file": r["fixed_from"], "Suggested": r["email"],
             "Problem": r["problem"]} for r in result["fixable"]]
    rows += [{"Name": r["name"], "In file": r["email"], "Suggested": fixes[r["key"]],
              "Problem": ("text after .com" if r["email"].lower().startswith(fixes[r["key"]].lower())
                          else "misspelt domain")}
             for r in recipients if r["key"] in fixes]
    n = len(rows)
    title = (f"🔤 {n} address{'es' if n != 1 else ''} can probably be corrected — "
             "misspelt domains, stray spaces, missing @ or .com …" if rows
             else "🔤 Corrections: none needed in the filtered rows")
    with st.expander(title):
        if rows:
            st.dataframe(pd.DataFrame(rows)[["In file", "Suggested", "Problem", "Name"]],
                         hide_index=True, width="stretch")
        use_fixes = st.checkbox("Send to the suggested addresses instead",
                                key=keep(f"{t}_typo_fix"))
        st.caption("Misspelt domains would bounce; invalid addresses aren't sent at all "
                   "unless corrected. Corrected addresses are marked ✏️ in the list below, "
                   "where any of them can still be unticked.")
    if not use_fixes:
        return recipients
    fixed, seen = [], {r["email"].lower() for r in recipients if r["key"] not in fixes}
    for r in recipients:
        if r["key"] in fixes:
            if fixes[r["key"]].lower() in seen:
                continue  # the corrected address is already on the list
            seen.add(fixes[r["key"]].lower())
            r = {**r, "email": fixes[r["key"]], "fixed_from": r["email"]}
        fixed.append(r)
    for r in result["fixable"]:
        if r["email"].lower() not in seen:
            seen.add(r["email"].lower())
            fixed.append(r)
    return fixed


def dataset_picker(df, ns, source_id, default_filters=None, settings=None,
                   problem_hint="Fix these in the file and upload it again.",
                   no_email_hint=""):
    """Pick recipients out of a table.

    Finds the email column, reports how many addresses were pulled (and what was
    skipped), lets the user filter rows by any other column, then hands the
    result to recipient_picker. default_filters: columns shown as filters
    straight away (None = detected area / city style columns).
    Returns the selected addresses.
    """
    t = f"{ns}_{source_id}"
    columns = list(df.columns)
    detected = dataset.detect_email_columns(df)
    settings = settings or st.expander("⚙️ Column settings")

    with settings:
        key = keep(f"{t}_email_cols")
        email_cols = st.multiselect(
            "Email column(s)", columns, key=key,
            help="Columns the addresses are read from. Pick more than one to "
                 "include addresses from each.",
            **_first_time(key, default=dataset.default_email_columns(detected)),
        )
        name_default = dataset.detect_name_column(df, email_cols)
        name_options = ["(none)"] + columns
        key = keep(f"{t}_name_col")
        name_col = st.selectbox(
            "Name column (shown next to each address)", name_options, key=key,
            **_first_time(key, index=name_options.index(name_default) if name_default else 0),
        )
        name_col = None if name_col == "(none)" else name_col
        st.caption("First rows of the file:")
        st.dataframe(df.head(10), width="stretch")

    if not email_cols:
        st.warning("Couldn't find a column with email addresses — choose it under "
                   f"⚙️ settings above.{no_email_hint}")
        return []

    merged = df.attrs.get("merged_rows") or []
    if merged:
        st.warning(
            f"Row{'s' if len(merged) > 1 else ''} {', '.join(map(str, merged[:10]))} "
            "seem to have swallowed the lines after them — usually a stray quote "
            "mark (\") in the file. Those lines aren't counted as separate rows; "
            "fix the quote and upload the file again."
        )

    full = dataset.extract_recipients(df, email_cols, name_col)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows in file", f"{full['rows']:,}")
    m2.metric("Unique emails", f"{len(full['recipients']):,}")
    m3.metric("Invalid emails", f"{full['rows_invalid']:,}")
    m4.metric("No email", f"{full['rows_blank']:,}")
    st.caption(
        f"Read from **{', '.join(email_cols)}**: {full['rows_valid']:,} rows with a valid "
        f"address + {full['rows_invalid']:,} with an invalid one + {full['rows_blank']:,} "
        f"blank = {full['rows']:,} rows. {full['addresses']:,} addresses found, "
        f"{full['duplicates']:,} repeats merged → {len(full['recipients']):,} unique."
        + (f" {len(full['fixable']):,} of the invalid ones can be corrected — see 🔤 below."
           if full["fixable"] else "")
    )

    extra = [c for c in detected if c not in email_cols]
    if extra:
        have = {r["key"] for r in full["recipients"]}
        left_out = [r for r in dataset.extract_recipients(df, extra)["recipients"]
                    if r["key"] not in have]
        if left_out:
            st.warning(
                f"{len(left_out):,} more addresses are in **{', '.join(extra)}**, which "
                "isn't being read. Add it under ⚙️ settings above to include them."
            )
    for col, n_no in dataset.flag_columns_with_no(df):
        st.warning(f"{n_no:,} rows have **{col}** set to FALSE / No and are included. "
                   f"Add {col} under *Filter by* to leave them out.")

    # ----- filters -----
    filterable = [c for c in columns if c not in email_cols]
    if default_filters is None:
        default_filters = dataset.detect_location_columns(df, email_cols)
    key = keep(f"{t}_filter_cols")
    filter_cols = st.multiselect(
        "Filter by", filterable, key=key, placeholder="Pick columns to filter on (e.g. Area)",
        help="Pick columns to filter on, then choose the values to keep. Each filter "
             "lists only the values left after the filters above it; leaving one "
             "empty keeps every value.",
        **_first_time(key, default=[c for c in default_filters if c in filterable]),
    )

    value_filters, text_filters = {}, {}
    for col in filter_cols:
        if df[col].map(dataset.norm).nunique() > MAX_FILTER_OPTIONS:
            text_filters[col] = st.text_input(
                f"{col} contains", key=keep(f"{t}_ft_{col}"),
                help="Comma-separate several terms to match any of them.",
            )
        else:
            # each filter lists only the values left after the filters above it
            base = dataset.apply_filters(df, value_filters, text_filters)
            value_filters[col] = _value_filter(df, base, col, f"{t}_f_{col}")

    matched = dataset.apply_filters(df, value_filters, text_filters)
    result = dataset.extract_recipients(matched, email_cols, name_col, detail_cols=filter_cols)
    filtering = any(value_filters.values()) or any(v.strip() for v in text_filters.values())
    if filtering:
        st.success(
            f"Filters match **{len(matched):,}** of {len(df):,} rows → "
            f"**{len(result['recipients']):,}** unique emails "
            f"({result['rows_invalid']:,} invalid, {result['rows_blank']:,} without an email)."
        )
    _problem_rows(
        result["problems"],
        f"⚠️ {len(result['problems']):,} {'matching ' if filtering else ''}rows with a "
        "missing or invalid email",
        problem_hint,
    )
    recipients = _corrections(full, result, t)

    return recipient_picker(recipients, ns, reset_token=(t, tuple(email_cols)))


def pick_sheet(data, filename, key):
    """Sheet selectbox for a multi-sheet workbook (defaulting to the first sheet
    that has an email column). Returns the sheet name, or None for CSV."""
    sheets = _sheet_names(data, filename)
    if len(sheets) < 2:
        return None
    default = next((i for i, s in enumerate(sheets)
                    if dataset.detect_email_columns(load_table(data, filename, s))), 0)
    return st.selectbox("Sheet", sheets, key=keep(key), **_first_time(key, index=default))


def file_picker(filename, data, ns, default_filters=None):
    """dataset_picker for an uploaded file. Returns selected addresses."""
    token = file_token(data)
    try:
        sheet = pick_sheet(data, filename, f"{ns}_{token}_sheet")
    except Exception as e:
        st.error(f"Couldn't open {filename}: {e}")
        return []

    settings = st.expander("⚙️ File & column settings")
    with settings:
        header_choice = st.selectbox(
            "Header row", HEADER_CHOICES, key=keep(f"{ns}_{token}_header"),
            help="Which row holds the column names. Auto-detect skips title rows "
                 "and spots files with no header at all.",
        )
    header_row = {"Auto-detect": "auto", "No header row": None}.get(
        header_choice, HEADER_CHOICES.index(header_choice) - 2)

    try:
        df = load_table(data, filename, sheet, header_row)
    except Exception as e:
        st.error(f"Couldn't read {filename}: {e}")
        return []
    if df.empty:
        st.warning(f"{filename} has no rows" + (f" on sheet {sheet}." if sheet else "."))
        return []

    return dataset_picker(
        df, ns, source_id=f"{token}_{sheet}_{header_row}",
        default_filters=default_filters, settings=settings,
        problem_hint="Fix these in the file and upload it again, or type the right "
                     "addresses into the box below.",
        no_email_hint=" If the addresses are on another sheet, pick it under Sheet." if sheet else "",
    )
