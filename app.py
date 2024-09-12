from pymongo import MongoClient
import jwt
from jwt.exceptions import ExpiredSignatureError
from datetime import datetime, timedelta
import hashlib
from flask import Flask, render_template, jsonify, request, redirect, url_for, make_response, flash, session
import json
from werkzeug.utils import secure_filename
from bson import ObjectId
import os
from os.path import join, dirname
from dotenv import load_dotenv

# Load environment variables
dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)

MONGODB_URI = os.environ.get("MONGODB_URI")
DB_NAME = os.environ.get("DB_NAME")

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['UPLOAD_FOLDER'] = './static/img/uploads'
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')  # Use env var or default

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]

# Define collections
admin_collection = db['admin']
products_collection = db['products']
cart_collection = db['cart']  # Collection for storing cart data
messages_collection = db['messages']  # Collection for storing messages
orders_collection = db['orders']
users_collection = db['users']

TOKEN_KEY = 'mytoken'
SECRET_KEY = os.environ.get('SECRET_KEY', 'SAYA')
ADMIN_KEY = 'admintoken'



@app.route('/', methods=['GET'])
def home():
    products = products_collection.find()
    return render_template('Home.html', products=products)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username_receive = request.form["username_give"]
        password_receive = request.form["password_give"]
        pw_hash = hashlib.sha256(password_receive.encode("utf-8")).hexdigest()
        result = db.users.find_one({"username": username_receive, "password": pw_hash})
        if result:
            payload = {"id": username_receive, "exp": datetime.utcnow() + timedelta(seconds=60 * 60 * 24)}
            token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
            response = jsonify({'result': 'success', 'token': token})
            response.set_cookie(TOKEN_KEY, token, httponly=True)
            return response
        else:
            return jsonify({'result': 'fail', 'msg': "Username atau password kamu tidak ditemukan!"})
    return render_template('Login.html')

@app.route('/user')
def user():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        name_info = db.users.find_one({'username': payload["id"]})
        print(name_info)  # Debugging
        products = list(products_collection.find())
        cart_items = list(cart_collection.find({'user_id': payload["id"]}))
        return render_template('User.html', 
                               name_info=name_info, 
                               products=products, 
                               cart_items=cart_items)
    except (ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('login'))


@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        user_id = payload["id"]

        data = request.json
        product_name = data.get('product_name')
        quantity = int(data.get('quantity'))

        # Menghapus simbol 'Rp', titik, dan koma untuk memastikan konversi yang benar
        price_str = data.get('price').replace('Rp', '').replace('.', '').replace(',', '')
        price = float(price_str)
        image_url = data.get('image_url')

        if not image_url:
            product = products_collection.find_one({'name': product_name})
            image_url = product.get('image_url') if product else ''

        item = {
            'user_id': user_id,
            'product_name': product_name,
            'quantity': quantity,
            'price': price,
            'total_price': price * quantity,
            'image_url': image_url
        }

        result = cart_collection.insert_one(item)
        item_id = str(result.inserted_id)

        return jsonify({
            'status': 'success',
            'message': 'Item berhasil ditambahkan ke keranjang',
            'product_name': product_name,
            'quantity': quantity,
            'price': "{:,.0f}".format(price),
            'item_id': item_id,
            'image_url': image_url
        })

    except (ExpiredSignatureError, jwt.exceptions.DecodeError):
        return jsonify({'status': 'fail', 'message': 'Pengguna tidak terautentikasi'})
    except Exception as e:
        return jsonify({'status': 'fail', 'message': f'Error dalam konversi data: {str(e)}'})

@app.route('/remove_from_cart/<item_id>', methods=['POST'])
def remove_from_cart(item_id):
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        user_id = payload["id"]

        # Pastikan ID produk adalah ObjectId yang valid
        if not ObjectId.is_valid(item_id):
            return jsonify({'status': 'fail', 'message': 'Invalid item ID'})

        # Hapus item dari keranjang
        result = cart_collection.delete_one({'_id': ObjectId(item_id), 'user_id': user_id})

        if result.deleted_count > 0:
            # Ambil kembali daftar item di keranjang
            cart_items = list(cart_collection.find({'user_id': user_id}))
            for item in cart_items:
                item['_id'] = str(item['_id'])  # Ubah ObjectId ke string untuk JSON
            return jsonify({'status': 'success', 'message': 'Item removed from cart', 'cart': cart_items})
        else:
            return jsonify({'status': 'fail', 'message': 'Item not found or user not authorized'})
    
    except (ExpiredSignatureError, jwt.exceptions.DecodeError) as e:
        return jsonify({'status': 'fail', 'message': 'User not authenticated'})
    except Exception as e:
        return jsonify({'status': 'fail', 'message': 'Error occurred: ' + str(e)})



@app.route('/place_order', methods=['POST'])
def place_order():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        user_id = payload["id"]

        data = request.json
        selected_items = data.get('selected_items')

        if not selected_items:
            return jsonify({'status': 'fail', 'message': 'Tidak ada produk yang dipilih.'})

        # Debugging: log data yang diterima
        print("Received data:", selected_items)

        # Validasi dan konversi harga menjadi float
        for item in selected_items:
            if 'product_name' not in item or 'quantity' not in item or 'total_price' not in item or 'image_url' not in item:
                return jsonify({'status': 'fail', 'message': 'Produk tidak memiliki informasi lengkap.'})
            try:
                item['total_price'] = float(item['total_price'])  # Pastikan ini adalah float
            except ValueError:
                return jsonify({'status': 'fail', 'message': 'Harga produk tidak valid.'})

        session['selected_items'] = selected_items

        return jsonify({'status': 'success'})
    
    except (ExpiredSignatureError, jwt.exceptions.DecodeError):
        return jsonify({'status': 'fail', 'message': 'Pengguna tidak terautentikasi'})
    except Exception as e:
        return jsonify({'status': 'fail', 'message': f'Error: {str(e)}'})


@app.route('/order', methods=['POST'])
def order():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        # Dekode JWT untuk mendapatkan informasi pengguna
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])

        # Cari informasi pengguna berdasarkan username dari payload JWT
        user_info = db.users.find_one({'username': payload['id']})
        
        if not user_info:
            return redirect(url_for('login'))

        # Ambil data dari form POST (dikirim dari modal sebelumnya)
        product_name = request.form.get('product_name')
        product_image = request.form.get('product_image')
        quantity = int(request.form.get('quantity'))  # Konversi ke integer
        total_price_str = request.form.get('total_price').replace('Rp ', '').replace('.', '').replace(',', '')  # Menghapus format
        total_price = float(total_price_str)  # Konversi ke float
        product_price_str = request.form.get('product_price').replace('Rp ', '').replace('.', '').replace(',', '')  # Menghapus format
        product_price = float(product_price_str)  # Konversi ke float

        # Cari produk di MongoDB berdasarkan nama produk
        product = db.products.find_one({'name': product_name})

        if not product:
            return "Produk tidak ditemukan", 404

        # Periksa apakah stok mencukupi
        if int(product['stock']) < quantity:
            return f"Stok tidak mencukupi. Stok saat ini: {product['stock']} kg", 400

        # Kurangi stok produk
        new_stock = int(product['stock']) - quantity

        # Perbarui stok di MongoDB
        db.products.update_one({'_id': product['_id']}, {'$set': {'stock': new_stock}})

        # Hitung total harga
        shipping_cost = 500000  # Misalkan ongkir tetap untuk contoh ini
        total_amount_due = total_price + shipping_cost

        # Ambil tanggal pemesanan dan estimasi pengiriman
        order_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        estimated_delivery_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')

        # Hitung total berat
        total_weight = quantity  # Asumsikan satuan sudah kg

        # Render halaman konfirmasi pesanan
        return render_template('order.html', 
                               product_name=product_name, 
                               product_image=product_image, 
                               quantity=quantity,
                               total_price=total_price, 
                               product_price=product_price,
                               shipping_cost=shipping_cost,
                               total_amount_due=total_amount_due,
                               user_info=user_info, 
                               order_date=order_date,
                               estimated_delivery_date=estimated_delivery_date,
                               total_weight=total_weight)

    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('login'))
    except ValueError as e:
        return f"Error processing data: {e}", 400




    
def format_rupiah(value):
    return "Rp {:,.0f}".format(value).replace(",", ".")

@app.route('/pay', methods=['GET'])
def pay():
    total_bayar = request.args.get('totalBelanja', '0')
    
    # Pastikan total_bayar adalah angka (float), lalu format ke rupiah
    try:
        total_bayar = float(total_bayar)
    except ValueError:
        total_bayar = 0  # Jika ada kesalahan parsing, total_bayar menjadi 0
    
    formatted_total_bayar = format_rupiah(total_bayar)
    
    return render_template('pay.html', total_bayar=formatted_total_bayar)


# Route untuk pengunggahan bukti pembayaran
@app.route('/upload_bukti', methods=['POST'])
def upload_bukti():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])

        if 'payment_proof' not in request.files:
            flash('Tidak ada file yang dipilih.')
            return redirect(url_for('pay'))

        file = request.files['payment_proof']

        if file.filename == '':
            flash('Tidak ada file yang dipilih.')
            return redirect(url_for('pay'))

        if file:
            # Amankan nama file dan simpan ke direktori static/img
            filename = secure_filename(file.filename)
            file_path = os.path.join('static/img', filename)  # Simpan di static/img
            file.save(file_path)
            
            # Simpan jalur lengkap ke dalam database
            file_url = f"/static/img/{filename}"  # Format jalur yang akan disimpan

            # Perbarui dokumen pesanan dengan bukti pembayaran
            order_id = request.form.get('order_id')
            orders_collection.update_one(
                {"_id": ObjectId(order_id)},
                {"$set": {"payment_proof": file_url, "status": "Bukti Pembayaran Diunggah"}}
            )

            flash('Bukti pembayaran berhasil diunggah.')
            return redirect(url_for('pesanan'))

    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('login'))




# Route untuk membuat pesanan
@app.route('/submit_order', methods=['POST'])
def submit_order():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        # Verifikasi token JWT
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])

        # Ambil data dari formulir
        nama = request.form['nama']
        nohp = request.form['nohp']
        order_date = request.form['order_date']
        estimated_delivery_date = request.form['estimated_delivery_date']
        shipping_location = request.form['shipping_location']
        shipping_cost = request.form['shipping_cost']
        detail_address = request.form['detail_address']
        total_weight = request.form['total_weight']
        total_price = request.form['total_price']
        final_price = request.form['final_price']
        product_name = request.form['product_name']
        product_image = request.form['product_image']
        map_points = request.form['map_points']
        estimasi_jarak = request.form['estimasi_jarak']
        estimasi_pengiriman = request.form['estimasi_pengiriman']

        # Buat dokumen untuk disimpan di database
        doc = {
            "nama": nama,
            "nohp": nohp,
            "order_date": order_date,
            "estimated_delivery_date": estimated_delivery_date,
            "shipping_location": shipping_location,
            "shipping_cost": shipping_cost,
            "detail_address": detail_address,
            "total_weight": total_weight,
            "total_price": total_price,
            "final_price": final_price,
            "product_name": product_name,
            "product_image": product_image,
            "map_points": map_points,
            "estimasi_jarak": estimasi_jarak,
            "estimasi_pengiriman": estimasi_pengiriman,
            "status": "Pesanan Diterima"
        }

        # Simpan ke database
        result = orders_collection.insert_one(doc)

        # Ambil ID pesanan yang baru disimpan
        order_id = str(result.inserted_id)

        # Redirect ke halaman pay.html dengan totalBelanja dan order_id
        return redirect(url_for('pay', totalBelanja=final_price, order_id=order_id))

    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        # Jika token JWT kadaluarsa atau tidak valid, arahkan ke halaman login
        return redirect(url_for('login'))


# @app.route('/pesanan', defaults={'order_id': None}, methods=['GET'])
# @app.route('/pesanan/<order_id>', methods=['GET'])
# def pesanan(order_id):
#     token_receive = request.cookies.get(TOKEN_KEY)
    
#     try:
#         # Verifikasi token JWT
#         payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])

#         if order_id:
#             # Ambil data pesanan dari database berdasarkan order_id
#             order = orders_collection.find_one({"_id": ObjectId(order_id)})
            
#             if order:
#                 # Render template dengan data pesanan
#                 return render_template('pesanan.html', order=order)
#             else:
#                 flash('Pesanan tidak ditemukan', 'danger')
#                 return redirect(url_for('order'))  # Redirect ke halaman order jika pesanan tidak ditemukan
#         else:
#             # Jika order_id tidak ada, lakukan tindakan yang sesuai
#             return render_template('pesanan.html')  # Atau redirect ke halaman lain
            
#     except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
#         flash('Session berakhir, silakan login kembali', 'danger')
#         return redirect(url_for('login'))


@app.route("/pesanan", methods=["GET"])
def pesanan():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        # Decode JWT untuk mendapatkan informasi pengguna
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])

        # Ambil data pengguna berdasarkan username dari JWT
        user = db.users.find_one({'username': payload['id']})

        if user:
            # Ambil pesanan hanya milik pengguna yang sedang login berdasarkan nama pengguna
            orders = list(db.orders.find({'nama': user['nama']}))
            # print(orders)
            # Konversi harga menjadi float jika diperlukan
            for order in orders:
                order['total_price'] = float(order.get('total_price', 0))
                order['final_price'] = float(order.get('final_price', 0))

            # Tampilkan halaman pesanan dengan data yang ditemukan
            return render_template('pesanan.html', orders=orders)
        else:
            return redirect(url_for('login'))
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('login'))
    



    












    
@app.route('/loginadmin')
def loginadmin():
    return render_template('LoginAdmin.html')

@app.route("/admin/login", methods=["POST"])
def admin_login():
    email_receive = request.form["email_give"]
    password_receive = request.form["password_give"]
    pw_hash = hashlib.sha256(password_receive.encode("utf-8")).hexdigest()
    result = admin_collection.find_one({"email": email_receive, "password": pw_hash})
    if result:
        payload = {"id": email_receive, "exp": datetime.utcnow() + timedelta(seconds=60 * 60 * 24)}
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        response = make_response(jsonify({"result": "success"}))
        response.set_cookie(ADMIN_KEY, token, httponly=True)
        return response
    else:
        return jsonify({"result": "fail", "msg": "Username atau password kamu tidak ditemukan!"})

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/admin")
def admin():
    token_receive = request.cookies.get(ADMIN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        
        # Ambil data produk
        products = list(products_collection.find({}))
        for product in products:
            product['_id'] = str(product['_id'])
            # Menyusun URL gambar produk dari folder static/img
            product['image_url'] = url_for('static', filename='img/' + product.get('image_url', 'default.png'))
        
        # Ambil informasi admin dari email yang diambil dari token
        name_info = admin_collection.find_one({"email": payload["id"]})
        
        # Ambil data pesan dari collection
        messages = list(messages_collection.find({}))
        for message in messages:
            message['_id'] = str(message['_id'])
        
        # Ambil data pesanan dari collection
        orders = list(orders_collection.find({}))
        for order in orders:
            order['_id'] = str(order['_id'])
            order['order_date'] = order.get('order_date', 'N/A')
            order['estimated_delivery_date'] = order.get('estimated_delivery_date', 'N/A')
            order['shipping_location'] = order.get('shipping_location', 'N/A')
            order['shipping_cost'] = order.get('shipping_cost', 'N/A')
            order['detail_address'] = order.get('detail_address', 'N/A')
            order['total_weight'] = order.get('total_weight', 'N/A')
            
            # Konversi total_price dan final_price ke float sebelum diformat
            try:
                order['total_price'] = float(order.get('total_price', 0))
                order['final_price'] = float(order.get('final_price', 0))
            except ValueError:
                order['total_price'] = 0
                order['final_price'] = 0
            
            order['product_name'] = order.get('product_name', 'N/A')
            
            # Menyusun URL gambar produk dan langsung menggunakan payment_proof dari database
            order['product_image'] = url_for('static', filename='img/' + order.get('product_image', 'default.png'))
            # Jangan tambahkan path static/img lagi ke payment_proof karena sudah lengkap
            order['payment_proof'] = order.get('payment_proof', '/static/img/default.png')
        
        # Ambil data pengguna dari collection
        users = list(users_collection.find({}))
        for i, user in enumerate(users):
            user['_id'] = str(user['_id'])
            user['index'] = i + 1  # Tambahkan index untuk nomor urut
        
        # Render template Admin.html dengan data yang sudah disiapkan
        return render_template(
            'Admin.html',
            name_info=name_info,
            products=products,
            messages=messages,
            orders=orders,
            users=users
        )
    
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('loginadmin'))
    
    


@app.route('/admin/notifications', methods=['GET'])
def admin_notifications():
    token_receive = request.cookies.get(ADMIN_KEY)
    if not token_receive:
        return jsonify({"error": "Admin not authenticated"}), 401

    try:
        # Decode JWT token untuk admin
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        admin_email = payload["id"]
        
        # Verifikasi admin dari koleksi admin_collection
        admin = admin_collection.find_one({"email": admin_email})
        if not admin:
            return jsonify({"error": "Admin not found"}), 404
        
        # Cek stok produk yang ada
        low_stock_products = db.products.find({"stock": {"$lt": 1000}})
        
        notifications = []
        for product in low_stock_products:
            notifications.append({
                "message": f"Stok {product['name']} berkurang, harap segera menambah stok.",
               
                "icon": "bi bi-exclamation-circle",
                
            })
        
        # Kirim notifikasi atau pesan jika tidak ada notifikasi
        if notifications:
            return jsonify(notifications), 200
        else:
            return jsonify([{
                "message": "No new notifications",
                "time": "",
                "icon": "bi bi-check-circle",
                "link": "#"
            }]), 200

    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token expired"}), 401
    except jwt.DecodeError:
        return jsonify({"error": "Token invalid"}), 401




@app.route('/admin/products/<product_id>', methods=['GET'])
def product_details(product_id):
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        admin_info = db.users.find_one({'username': payload['id'], 'role': 'admin'})

        if not admin_info:
            return redirect(url_for('login'))

        product = db.products.find_one({'_id': ObjectId(product_id)})
        if not product:
            return "Product not found", 404

        return render_template('admin_product_details.html', product=product)
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('login'))



@app.route('/update_order_status', methods=['POST'])
def update_order_status():
    token_receive = request.cookies.get(TOKEN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])

        order_id = request.form.get('order_id')
        new_status = request.form.get('status')

        # Update order status in the database
        orders_collection.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"status": new_status}}
        )

        flash('Status pesanan berhasil diperbarui.')
        return redirect(url_for('admin'))

    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('login'))

@app.route('/akun')
def akun():
    token_receive = request.cookies.get(TOKEN_KEY)
    if not token_receive:
        return redirect(url_for('login'))
    
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        username = payload['id']
        
        user = db.users.find_one({"username": username})
        if user:
            return render_template('akun.html', user=user)
        else:
            return redirect(url_for('login'))
    except jwt.ExpiredSignatureError:
        return redirect(url_for('login'))
    except jwt.exceptions.DecodeError:
        return redirect(url_for('login'))
    





@app.route('/update_user', methods=['POST'])
def update_user():
    token_receive = request.cookies.get(TOKEN_KEY)
    if not token_receive:
        return redirect(url_for('login'))
    
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        username = payload['id']
        
        user_data = {
            "nama": request.form['nama'],
            "email": request.form['email'],
            "nohp": request.form['nohp']
        }

        db.users.update_one({"username": username}, {"$set": user_data})

        return redirect(url_for('akun'))
    except jwt.ExpiredSignatureError:
        return redirect(url_for('login'))
    except jwt.exceptions.DecodeError:
        return redirect(url_for('login'))



    
# @app.route("/admin/users")
# def admin_users():
#     token_receive = request.cookies.get(ADMIN_KEY)
#     try:
#         payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        
#         # Ambil data pengguna dari koleksi users
#         users = list(users_collection.find({}))
#         for i, user in enumerate(users):
#             user['_id'] = str(user['_id'])
#             user['index'] = i + 1
        
#         return render_template('Admin_Users.html', users=users)
#     except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
#         return redirect(url_for('loginadmin'))

@app.route("/admin/delete_user/<user_id>", methods=["POST"])
def delete_user(user_id):
    token_receive = request.cookies.get(ADMIN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        
        # Hapus pengguna berdasarkan ID
        result = users_collection.delete_one({"_id": ObjectId(user_id)})
        
        if result.deleted_count > 0:
            flash("Pengguna berhasil dihapus!", "success")
        else:
            flash("Pengguna tidak ditemukan!", "danger")
        
        return redirect(url_for('admin'))  # Update 'admin' to the correct endpoint if needed
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('loginadmin'))

@app.route("/admin/update_kurir_position/<id_order>", methods=["GET"])
def update_kurir_position(id_order):
    token_receive = request.cookies.get(ADMIN_KEY)
    try:
        order = db.orders.find_one({"_id": ObjectId(id_order)})
        return render_template('update_kurir_position.html', order=order)
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('loginadmin'))

@app.route("/admin/set_kurir_position/<id_order>/<data_points>", methods=["GET"])
def set_kurir_position(id_order,data_points):
    token_receive = request.cookies.get(ADMIN_KEY)
    try:
        # order = db.orders.find_one({"_id": ObjectId(id_order)})
        db.orders.update_one(
            {"_id": ObjectId(id_order)},    # Filter by order ID
            {"$set": {"kurir_position": data_points}}  # Set new value for 'kurir_position'
        )
        return jsonify({'valid':True})
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('loginadmin'))



@app.route('/add_product', methods=['POST'])
def add_product():
    token_receive = request.cookies.get(ADMIN_KEY)
    try:
        payload = jwt.decode(token_receive, SECRET_KEY, algorithms=["HS256"])
        if 'productImage' not in request.files:
            return "No file part", 400

        file = request.files['productImage']
        if file.filename == '':
            return "No selected file", 400

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join('static/img', filename))

            # Extract other product details
            product_name = request.form['productName']
            product_price = float(request.form['productPrice'])
            product_stock = int(request.form['productStock'])
            product_description = request.form['productDescription']

            # Insert the new product into the database
            products_collection.insert_one({
                'name': product_name,
                'price': product_price,
                'stock': product_stock,
                'description': product_description,
                'image': filename
            })

            return redirect(url_for('admin'))
        else:
            return "Invalid file type", 400
    except (jwt.ExpiredSignatureError, jwt.exceptions.DecodeError):
        return redirect(url_for('loginadmin'))

@app.route('/edit_product/<product_id>', methods=['POST'])
def edit_product(product_id):
    form = request.form
    file = request.files.get('productImage')
    
    update_fields = {
        'name': form['productName'],
        'price': form['productPrice'],
        'stock': form['productStock'],
        'description': form['productDescription'],
    }

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        # Create directory if it does not exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        file.save(file_path)
        update_fields['image'] = filename

    db.products.update_one(
        {'_id': ObjectId(product_id)},
        {'$set': update_fields}
    )

    return jsonify(success=True)

@app.route('/delete_product/<product_id>', methods=['GET'])
def delete_product(product_id):
    db.products.delete_one({'_id': ObjectId(product_id)})
    return jsonify(success=True)

@app.route('/product/<product_id>')
def get_product(product_id):
    product = db.products.find_one({'_id': ObjectId(product_id)})
    if product:
        product['_id'] = str(product['_id'])
        return jsonify(product)
    else:
        return jsonify({'result': 'fail', 'msg': 'Product not found!'})

@app.route("/sign_up")
def sign_up():
    return render_template('SignUp.html')

@app.route('/sign_up/save', methods=['POST'])
def signup_save():
    username_receive = request.form.get('username_give')
    password_receive = request.form.get('password_give')
    nama_receive = request.form.get('nama_give')
    email_receive = request.form.get('email_give')
    nohp_receive = request.form.get('nomorhp_give')
    password_hash = hashlib.sha256(password_receive.encode('utf-8')).hexdigest()
    doc = {
        "nama": nama_receive,
        "username": username_receive,
        "password": password_hash,
        "email": email_receive,
        "nohp": nohp_receive
    }
    db.users.insert_one(doc)
    return jsonify({'result': 'success'})

@app.route('/submit_message_user', methods=['POST'])
def submit_message_user():
    # Ambil data dari form
    name = request.form['name']
    phone = request.form['phone']
    message = request.form['message']
    
    # Simpan pesan ke dalam koleksi MongoDB
    messages_collection.insert_one({
        'name': name,
        'phone': phone,
        'message': message,
        'timestamp': datetime.utcnow()
    })
    
    # Redirect kembali ke halaman utama
    return redirect(url_for('user'))

@app.route('/submit_message', methods=['POST'])
def submit_message():
    # Ambil data dari form
    name = request.form['name']
    phone = request.form['phone']
    message = request.form['message']
    
    # Simpan pesan ke dalam koleksi MongoDB
    messages_collection.insert_one({
        'name': name,
        'phone': phone,
        'message': message,
        'timestamp': datetime.utcnow()
    })
    
    # Redirect kembali ke halaman utama
    return redirect(url_for('home'))



if __name__ == '__main__':
    app.run('0.0.0.0', port=5000, debug=True)
