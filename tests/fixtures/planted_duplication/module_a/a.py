def process_data_a(items: list[int]) -> int:
    """This function processes data in module A."""
    result = 0
    for item in items:
        if item % 2 == 0:
            result += item * 2
        else:
            result -= item
    return result

def some_other_func_a():
    pass
