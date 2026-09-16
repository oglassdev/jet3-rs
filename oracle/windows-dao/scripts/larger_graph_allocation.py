"""Compare selected Memo placement while accounting for every captured page."""
import system_catalog as catalog


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def complete_allocation(path, raw):
    data = path.read_bytes()
    analysis = catalog.analyze_checkpoint(data)
    accounted = {0, 1} | set(raw['free_pages'])
    for table in analysis['tables'].values():
        definition = table['definition']
        accounted.update(definition['pages'])
    for record in raw['maps'].values():
        accounted.add(record['record']['locator']['page'])
        accounted.update(page for page in record['record']['references'] if page)
        accounted.update(record['members'])
    require(accounted == set(range(raw['page_count'])), 'every page has an allocation role')
    require(raw['free_pages'] == raw['maps']['global']['members'], 'complete global free-page inventory')


def compare(rust_path, native_path, rust, native, selected_columns, history):
    # The raw observer has already proved unique ownership, complete payload
    # reachability, active-slot coverage and every payload byte in both images.
    require(set(rust['maps']) == set(native['maps']), 'complete map role inventory')
    selected_roles = set()
    for table_name, column_name in selected_columns:
        column = next(column for column in rust['tables'][table_name]['columns']
                      if column['name'] == column_name)
        require(column['type'] in ('Memo', 'LongBinary'), 'selected payload column')
        key = f"{table_name}/lval/{column['ordinal']}"
        selected_roles.update(key + '/' + role for role in ('owned', 'available'))
    for key in rust['maps']:
        left, right = rust['maps'][key], native['maps'][key]
        if key not in selected_roles and key != 'global':
            require(left == right, f'unselected allocation exact: {key}')
            continue
        stable = lambda value: {k: v for k, v in value['record'].items()
                                if k not in ('raw_hex', 'members', 'outside_eof')}
        require(stable(left) == stable(right), f'map framing and storage exact: {key}')
        # Inline map headers are fixed; the bitmap is fully decoded and checked.
        # Indirect rows contain only references, whose complete bytes stay exact.
        length = 10 if left['record']['kind'] == 0 else None
        require(left['record']['raw_hex'][:length] == right['record']['raw_hex'][:length],
                f'map header exact: {key}')
        if key != 'global':
            require(len(left['members']) == len(right['members']), f'selected allocation count: {key}')
            history.update(left['members'])
            history.update(right['members'])
    for path, raw in ((rust_path, rust), (native_path, native)):
        complete_allocation(path, raw)
    free_difference = set(rust['free_pages']) ^ set(native['free_pages'])
    require(free_difference <= history, 'free-page differences belong to selected payload history')
    eof_difference = set(range(min(rust['page_count'], native['page_count']),
                               max(rust['page_count'], native['page_count'])))
    require(eof_difference <= history, 'EOF difference belongs to selected payload history')
    return {'selected_columns': sorted(selected_columns), 'selected_page_history': sorted(history),
            'rust_free_pages': rust['free_pages'], 'native_free_pages': native['free_pages'],
            'rust_page_count': rust['page_count'], 'native_page_count': native['page_count'],
            'selected_maps': {key: {'rust': rust['maps'][key], 'native': native['maps'][key]}
                              for key in sorted(selected_roles)}}
