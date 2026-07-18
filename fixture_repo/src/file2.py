def totally_different_function_c():
    import math
    return math.pi * 2

def duplicated_function_x():
    """This function calculates the nth fibonacci number using an iterative approach.
    It is slightly verbose to ensure there is enough substance for semantic drift to catch it."""
    a, b = 0, 1
    for _ in range(10):
        a, b = b, a + b
    return b

def totally_different_function_d(name):
    print(f"Hello, {name}!")
    return len(name)
