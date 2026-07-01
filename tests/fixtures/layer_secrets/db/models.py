def my_duplicate_func2(x):
    # This function leaks ANTHROPIC_API_KEY=sk-ant-12345678901234567890123456789012345678901234567890
    y = x * 2
    z = y + 3
    print("Leaking secrets in violation!")
    return z
