"""Streamlit pieces for choosing recipients.

recipient_picker  — the select / deselect list shared by every recipient mode
dataset_picker    — recipients from a table: email column, column filters, counts
file_picker       — dataset_picker for a freshly uploaded CSV / Excel file
"""
import hashlib
import re

import pandas as pd
import streamlit as st

import dataset

PAGE_SIZE = 100
# columns with more distinct values than this get a "contains" box, not a list
MAX_FILTER_OPTIONS = 300

HEADER_CHOICES = ["Auto-detect", "No header row"] + [f"Row {i}" for i in range(1, 11)]


@st.cache_data(show_spinner=False, max_entries=20)
def load_table(data, filename, sheet=None, header_row="auto"):
    return dataset.read_table(data, filename, sheet, header_row)


def file_token(data):
    return hashlib.md5(data).hexdigest()[:12]


def _md_escape(text):
    return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~$])", r"\\\1", text)


def _clear_checkboxes(ns, emails=None):
    prefix = f"{ns}_chk_"
    for key in [k for k in st.session_state.keys() if str(k).startswith(prefix)]:
        if emails is None or key[len(prefix):] in emails:
            del st.session_state[key]


# ---------- shared select / deselect list ----------
def recipient_picker(recipients, ns, reset_token):
    """Let the user tick / untick individual recipients (all ticked to start).

    recipients: [{"email", "name", "detail"?}] with unique emails.
    Choices are remembered per address until reset_token changes.
    Returns the ticked addresses, in list order.
    """
    sel_key, page_key = f"{ns}_selections", f"{ns}_page"
    search_key, last_search_key = f"{ns}_search", f"{ns}_last_search"
    prefix = f"{ns}_chk_"

    if st.session_state.get(f"{ns}_token") != reset_token:
        st.session_state[f"{ns}_token"] = reset_token
        st.session_state[sel_key] = {}
        st.session_state[page_key] = 0
        st.session_state[last_search_key] = ""
        st.session_state.pop(search_key, None)
        _clear_checkboxes(ns)

    selections = st.session_state[sel_key]
    for r in recipients:
        # a checkbox clicked on the last run holds the newest value
        if prefix + r["email"] in st.session_state:
            selections[r["email"]] = st.session_state[prefix + r["email"]]
        selections.setdefault(r["email"], True)

    total = len(recipients)
    selected_count = sum(1 for r in recipients if selections[r["email"]])
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
                    selections[r["email"]] = value
                _clear_checkboxes(ns, {r["email"] for r in filtered})
                st.rerun()

        st.text_input("Search by name or email", key=search_key)
        if search:
            st.caption(f"Select all / Deselect all apply to the {len(filtered)} matches.")

        # reset to page 0 when search or the list changes
        list_sig = hashlib.md5("\n".join(r["email"] for r in recipients).encode()).hexdigest()
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
            key = prefix + r["email"]
            if key not in st.session_state:
                st.session_state[key] = selections[r["email"]]
            label = f"{r['name']}  —  {r['email']}" if r["name"] else r["email"]
            if r.get("detail"):
                label += f"  ·  {r['detail']}"
            st.checkbox(_md_escape(label), key=key)

    return [r["email"] for r in recipients if selections[r["email"]]]


# ---------- recipients from a table ----------
def _value_filter(df, base, col, key):
    """Multiselect of a column's values, limited to the rows left in `base`."""
    keys, display, counts = dataset.value_options(base[col])
    chosen = st.session_state.get(key, [])
    if any(k not in counts for k in chosen):
        # keep earlier choices visible (with a 0 count) so they can be removed
        _, all_display, _ = dataset.value_options(df[col])
        for k in chosen:
            if k not in counts:
                keys.append(k)
                display[k] = all_display.get(k, k)
    return st.multiselect(
        col, keys, key=key, placeholder="All",
        format_func=lambda k: f"{display.get(k) or '(blank)'}  ({counts.get(k, 0)})",
    )


def _problem_rows(problems, title):
    if not problems:
        return
    with st.expander(title):
        st.dataframe(
            pd.DataFrame(problems).rename(columns={
                "row": "Row in file", "name": "Name", "value": "Email cell", "issue": "Issue",
            }),
            hide_index=True, width="stretch",
        )
        st.caption("Fix these in the file and re-upload, or type the right address "
                   "into the manual box.")


def dataset_picker(df, ns, source_id, default_filters=None, settings=None):
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
        email_cols = st.multiselect(
            "Email column(s)", columns, default=detected[:1], key=f"{t}_email_cols",
            help="Columns the addresses are read from. Pick more than one to "
                 "include addresses from each.",
        )
        name_default = dataset.detect_name_column(df, email_cols)
        name_options = ["(none)"] + columns
        name_col = st.selectbox(
            "Name column (shown next to each address)", name_options,
            index=name_options.index(name_default) if name_default else 0,
            key=f"{t}_name_col",
        )
        name_col = None if name_col == "(none)" else name_col
        st.caption("First rows of the file:")
        st.dataframe(df.head(10), width="stretch")

    if not email_cols:
        st.warning("Couldn't find an email column — choose it under ⚙️ Column settings.")
        return []
    extra = [c for c in detected if c not in email_cols]
    if extra:
        st.caption(f"Other columns that look like emails: {', '.join(extra)} — add them "
                   "under ⚙️ Column settings to include them.")

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
    )

    # ----- filters -----
    filterable = [c for c in columns if c not in email_cols]
    if default_filters is None:
        default_filters = dataset.detect_location_columns(df, email_cols)
    filter_cols = st.multiselect(
        "Filter by", filterable,
        default=[c for c in default_filters if c in filterable],
        key=f"{t}_filter_cols",
        help="Pick columns to filter on, then choose the values to keep. Each filter "
             "lists only the values left after the filters above it; leaving one "
             "empty keeps every value.",
    )

    text_cols = {c for c in filter_cols if df[c].map(dataset.norm).nunique() > MAX_FILTER_OPTIONS}
    value_filters, text_filters = {}, {}
    for col in filter_cols:
        if col in text_cols:
            text_filters[col] = st.text_input(
                f"{col} contains", key=f"{t}_ft_{col}",
                help="Comma-separate several terms to match any of them.",
            )
        else:
            # each filter lists only the values left after the filters above it
            base = dataset.apply_filters(df, value_filters, text_filters)
            value_filters[col] = _value_filter(df, base, col, f"{t}_f_{col}")

    matched = dataset.apply_filters(df, value_filters, text_filters)
    result = dataset.extract_recipients(matched, email_cols, name_col, detail_cols=filter_cols)
    filtering = len(matched) != len(df) or any(value_filters.values()) or any(
        v.strip() for v in text_filters.values())
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
    )
    recipients = _typo_fixes(result["recipients"], t)

    return recipient_picker(recipients, ns, reset_token=(t, tuple(email_cols)))


def _typo_fixes(recipients, t):
    """Flag addresses whose domain looks misspelt (gmai.com …) and, if the user
    opts in, swap in the corrected address. Returns the recipient list to use."""
    fixes = {r["email"]: dataset.suggest_domain_fix(r["email"]) for r in recipients}
    fixes = {e: f for e, f in fixes.items() if f}
    if not fixes:
        return recipients
    with st.expander(f"🔤 {len(fixes)} addresses look like a misspelt email domain "
                     "(e.g. gmai.com) and will probably bounce"):
        st.dataframe(
            pd.DataFrame([{"Name": r["name"], "Address in file": r["email"],
                           "Suggested": fixes[r["email"]]}
                          for r in recipients if r["email"] in fixes]),
            hide_index=True, width="stretch",
        )
        use_fixes = st.checkbox("Send to the suggested addresses instead", key=f"{t}_typo_fix")
    if not use_fixes:
        return recipients
    fixed, seen = [], set()
    for r in recipients:
        email = fixes.get(r["email"], r["email"])
        if email.lower() not in seen:
            seen.add(email.lower())
            fixed.append({**r, "email": email})
    return fixed


def file_picker(uploaded, ns, default_filters=()):
    """dataset_picker for a file from st.file_uploader. Returns selected addresses."""
    data = uploaded.getvalue()
    token = file_token(data)
    try:
        sheets = dataset.sheet_names(data, uploaded.name)
    except Exception as e:
        st.error(f"Couldn't open {uploaded.name}: {e}")
        return []
    sheet = None
    if len(sheets) > 1:
        sheet = st.selectbox("Sheet", sheets, key=f"{ns}_{token}_sheet")

    settings = st.expander("⚙️ File & column settings")
    with settings:
        header_choice = st.selectbox(
            "Header row", HEADER_CHOICES, key=f"{ns}_{token}_header",
            help="Which row holds the column names. Auto-detect skips title rows "
                 "and spots files with no header at all.",
        )
    header_row = {"Auto-detect": "auto", "No header row": None}.get(
        header_choice, HEADER_CHOICES.index(header_choice) - 2)

    try:
        df = load_table(data, uploaded.name, sheet, header_row)
    except Exception as e:
        st.error(f"Couldn't read {uploaded.name}: {e}")
        return []
    if df.empty:
        st.warning(f"{uploaded.name} has no rows.")
        return []

    return dataset_picker(df, ns, source_id=f"{token}_{sheet}_{header_row}",
                          default_filters=default_filters, settings=settings)
