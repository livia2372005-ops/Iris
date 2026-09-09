"""Example calculation script to test iris run CLI."""

def multiply(a, b):
    return a * b

def main():
    val = 10
    product = multiply(val, 5)
    print(f"Computed product: {product}")

if __name__ == "__main__":
    main()
