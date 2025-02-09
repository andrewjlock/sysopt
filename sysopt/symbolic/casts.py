"""Operation registry for casting between different symbol types."""

_registry = {}


def register(from_type, to_type):
    def decorator(the_caster):
        if from_type not in _registry:
            _registry[from_type] = {}
        else:
            assert to_type not in _registry[from_type], \
                f'Cast from {from_type} to {to_type} is already defined'

        _registry[from_type][to_type] = the_caster
        return the_caster

    return decorator


def cast_type(var, to_type=None):
    from_type = type(var)
    if to_type is None:
        try:
            casters = _registry[from_type]
        except KeyError as ex:
            msg = f'Don\'t know how to cast {from_type}.'
            raise NotImplementedError(msg) from ex

        if len(casters) != 1:
            msg = f'Don\'t know how to uniquely cast from {from_type}'
            raise TypeError(msg)

        caster, = casters.values()
    else:
        try:
            caster = _registry[from_type][to_type]
        except KeyError as ex:
            msg = f'Don\'t know how to cast from {from_type} to {to_type}'
            raise NotImplementedError(msg) from ex

    return caster(var)


def cast_like(var, var_like):
    to_type = type(var_like)
    return cast_type(var, to_type)


