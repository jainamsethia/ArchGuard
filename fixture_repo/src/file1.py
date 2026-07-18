def unique_function_a():
    x = 10
    y = 20
    z = x + y
    return z * 2

def duplicated_function_x():
    """This function calculates the nth fibonacci number using an iterative approach.
    It is slightly verbose to ensure there is enough substance for semantic drift to catch it."""
    a, b = 0, 1
    for _ in range(10):
        a, b = b, a + b
    return b

def another_unique_function_b():
    result = []
    for i in range(100):
        if i % 2 == 0:
            result.append(i ** 2)
    return result
