def process_data_b(elements: list[int]) -> int:
    """This function processes data in module B."""
    total = 0
    for element in elements:
        if element % 2 == 0:
            total += element * 2
        else:
            total -= element
    return total

def some_other_func_b():
    pass
