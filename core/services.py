from .models import Product, Order, OrderItem

def create_order(user, product_id, quantity):
    product = Product.objects.get(id=product_id)

    if product.stock < quantity:
        raise Exception("Not enough stock")

    product.stock -= quantity
    product.save()

    order = Order.objects.create(user=user)
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=quantity
    )

    return order