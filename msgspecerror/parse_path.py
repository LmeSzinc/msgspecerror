KEY_at = ' - at `$'
KEY_at_key_in = ' - at `key` in `$'
KEY_at_check = '- at `$'


def _path_split_key(path):
    """
    Args:
        path (str): [...].RepairThreshold[0].value

    Yields:
        str: ..., RepairThreshold[0].value
    """
    if '[...]' in path:
        for index, part in enumerate(path.split('[...]')):
            if index:
                yield '...'
            if part:
                yield part
    # no dict key
    else:
        yield path


def _path_split_index(path):
    """
    Args:
        path (str): RepairThreshold[0].value

    Yields:
        int | str:
    """
    prefix = ''
    while 1:
        field, sep, remain = path.partition('[')
        if not sep:
            break
        index, sep, remain = remain.partition(']')
        if not sep:
            break
        try:
            index = int(index)
            # [0]
            if prefix:
                yield prefix + field
                prefix = ''
            elif field:
                yield field
            yield index
            path = remain
        except ValueError:
            # Invalid index like: [abc], [[], []]
            # accumulate consecutive non-numeric brackets into prefix
            prefix += f'{field}[{index}]'
            path = remain

    # no list index — preserve unparseable bracket content as field name
    if prefix:
        yield prefix + path
    elif path:
        yield path


def _path_split_part(path):
    """
    Args:
        path (str): OpsiGeneral.RepairThreshold

    Yields:
        str:
    """
    if '.' in path:
        for part in path.split('.'):
            if part:
                yield part
    else:
        yield path


def _path_split(path):
    """
    Args:
        path (str): Note that $. and $ should be removed before input
            OpsiGeneral.RepairThreshold
            [...].RepairThreshold[0].value

    Yields:
        str | int:
    """
    for field_index_key in _path_split_key(path):
        if field_index_key == '...':
            yield field_index_key
            continue
        for field_index in _path_split_index(field_index_key):
            if type(field_index) is int:
                yield field_index
                continue
            yield from _path_split_part(field_index)


def get_error_path(error):
    """
    Args:
        error (str):

    Returns:
        tuple[Union[int, str], ...]:
    """
    # Object contains unknown field `RepairThresho` ld1` - at `$.opsi`
    # Object missing required field `id`
    if error.startswith('Object missing required field ') or error.startswith('Object contains unknown field '):
        left, sep, right = error[30:].partition(KEY_at)
        if sep:
            field = left
            error = right
            if len(field) >= 2 and field.startswith('`') and field.endswith('`'):
                field = field[1:-1]
        else:
            # No path suffix: the field name is the entire path
            field = left
            if len(field) >= 2 and field.startswith('`') and field.endswith('`'):
                field = field[1:-1]
            return (field,)
        is_dict_key = False
    else:
        field = ''
        # Expected `MyCustomClass`, got `str` - at `$.custom_field`
        # check KEY_at first, because this is the most common error
        _, sep, right = error.partition(KEY_at)
        if sep:
            error = right
            is_dict_key = False
        else:
            # When having invalid dict key, we define a new special path "...key"
            # Expected `str`, got `int` - at `key` in `$.member_map`
            # will be parsed as ('member_map', '...key')
            _, sep, right = error.partition(KEY_at_key_in)
            if sep:
                error = right
                is_dict_key = True
            else:
                # no path
                return ()

    if error.endswith('`'):
        error = error[:-1]
    # path startswith `$.` or '$'
    # KEY_at and KEY_at_key_in endswith `$`
    # so here we need to remove `.`
    if error.startswith('.'):
        error = error[1:]

    # pydantic style that tells you ('custom_field', 'id') is missing
    if field:
        path = list(_path_split(error))
        path.append(field)
        return tuple(path)
    elif is_dict_key:
        path = list(_path_split(error))
        path.append('...key')
        return tuple(path)
    else:
        return tuple(_path_split(error))
