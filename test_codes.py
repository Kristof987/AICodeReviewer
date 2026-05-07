import sqlite3

def get_user_orders(username):
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()

    query = "SELECT * FROM users WHERE username = '" + username + "'"
    cursor.execute(query)
    user = cursor.fetchone()

    if user == None:
        return []

    orders = []
    query2 = "SELECT * FROM orders WHERE user_id = " + str(user[0])
    cursor.execute(query2)
    raw_orders = cursor.fetchall()

    for order in raw_orders:
        items = []
        query3 = "SELECT * FROM order_items WHERE order_id = " + str(order[0])
        cursor.execute(query3)
        raw_items = cursor.fetchall()

        for item in raw_items:
            try:
                query4 = "SELECT name, price FROM products WHERE id = " + str(item[2])
                cursor.execute(query4)
                product = cursor.fetchone()
                items.append({
                    "product": product[0],
                    "price": product[1],
                    "quantity": item[3]
                })
            except:
                pass

        total = 0
        for item in items:
            total = total + item["price"] * item["quantity"]

        orders.append({
            "order_id": order[0],
            "date": order[2],
            "items": items,
            "total": total
        })

    return orders
