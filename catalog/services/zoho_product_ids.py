"""Extract stable Zoho Commerce ids from product JSON (list or detail payloads)."""


def extract_zoho_category_id_from_detail(detail: dict) -> str:
    """Best-effort Zoho category id from product detail/list JSON."""
    if not isinstance(detail, dict):
        return ''
    blob = detail.get('product') or detail.get('item') or detail.get('data') or detail
    if not isinstance(blob, dict):
        return ''

    def _from_category_obj(obj) -> str:
        if not isinstance(obj, dict):
            return ''
        for key in ('category_id', 'id', 'product_category_id'):
            v = obj.get(key)
            if v not in (None, '', [], {}):
                return str(v).strip()
        return ''

    for key in ('category_id', 'product_category_id', 'primary_category_id'):
        v = blob.get(key)
        if v not in (None, '', [], {}):
            return str(v).strip()

    cat = blob.get('category')
    if cat is not None:
        if isinstance(cat, dict):
            out = _from_category_obj(cat)
            if out:
                return out
        elif isinstance(cat, str) and cat.strip().isdigit():
            return cat.strip()

    for list_key in ('categories', 'product_categories', 'category_list'):
        rows = blob.get(list_key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                out = _from_category_obj(row)
                if out:
                    return out
            elif isinstance(row, str) and row.strip():
                return row.strip()
    return ''


def extract_zoho_collection_id_from_detail(detail: dict) -> str:
    """Best-effort Zoho collection id from product detail/list JSON."""
    if not isinstance(detail, dict):
        return ''
    blob = detail.get('product') or detail.get('item') or detail.get('data') or detail
    if not isinstance(blob, dict):
        return ''

    def _from_collection_obj(obj) -> str:
        if not isinstance(obj, dict):
            return ''
        for key in ('collection_id', 'id'):
            v = obj.get(key)
            if v not in (None, '', [], {}):
                return str(v).strip()
        return ''

    for key in ('collection_id', 'primary_collection_id'):
        v = blob.get(key)
        if v not in (None, '', [], {}):
            return str(v).strip()

    coll = blob.get('collection')
    if isinstance(coll, dict):
        out = _from_collection_obj(coll)
        if out:
            return out

    for list_key in ('collections', 'collection_list'):
        rows = blob.get(list_key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                out = _from_collection_obj(row)
                if out:
                    return out
            elif isinstance(row, str) and row.strip():
                return row.strip()
    return ''
