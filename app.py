import os
import uuid
import stripe
from flask import Flask, render_template, request, redirect, session
from google.cloud import storage, firestore
from google.api_core.exceptions import NotFound


app = Flask(__name__)
# Secret key required for Flask login sessions
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "eco-workshop-secret-key")


# Admin password configured via Cloud Run environment variable
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


# Initialize Google Cloud clients
db = firestore.Client()
bucket = storage.Client().bucket(os.environ["BUCKET_NAME"])


# Initialize Stripe with Test Secret Key
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")




# 1. STOREFRONT: Browse, Search, Filter & Conditional Admin View
@app.route("/")
def index():
   q = request.args.get("q", "").strip().lower()
   cat = request.args.get("cat", "").strip()
   msg = request.args.get("msg", "")
   err = request.args.get("err", "")


   docs = db.collection("products").stream()
   items = [doc.to_dict() | {"id": doc.id} for doc in docs]


   if cat:
       items = [p for p in items if p.get("category") == cat]
   if q:
       items = [p for p in items if q in p.get("name", "").lower()]


   return render_template(
       "index.html",
       products=items,
       q=q,
       cat=cat,
       msg=msg,
       err=err,
       is_admin=session.get("is_admin", False)
   )




# 2. ADMIN AUTHENTICATION: Login & Logout
@app.route("/admin/login", methods=["POST"])
def admin_login():
   if request.form.get("password") == ADMIN_PASSWORD:
       session["is_admin"] = True
       return redirect("/?msg=Admin+mode+unlocked")
   return redirect("/?err=Incorrect+admin+password")




@app.route("/admin/logout")
def admin_logout():
   session.pop("is_admin", None)
   return redirect("/?msg=Logged+out+of+admin+mode")




# 3. ADMIN ONLY: Add Product (With Duplicate Prevention)
@app.route("/admin/add", methods=["POST"])
def add_product():
   if not session.get("is_admin"):
       return redirect("/?err=Unauthorised+access")


   name = request.form["name"].strip()


   # Check Firestore for existing product with the same name (case-insensitive)
   existing_docs = db.collection("products").stream()
   for doc in existing_docs:
       if doc.to_dict().get("name", "").strip().lower() == name.lower():
           return redirect(f"/?err=Product+'{name}'+already+exists.+Duplicate+blocked.")


   img = request.files["photo"]
   unique_filename = f"{uuid.uuid4()}_{img.filename}"
   blob = bucket.blob(unique_filename)
   blob.upload_from_file(img, content_type=img.content_type)


   db.collection("products").add({
       "name": name,
       "category": request.form["category"],
       "price": float(request.form["price"]),
       "desc": request.form["desc"].strip(),
       "image_url": blob.public_url
   })
   return redirect(f"/?msg=Added+'{name}'+to+store")




# 4. ADMIN ONLY: Edit Product Price & Description
@app.route("/admin/edit/<product_id>", methods=["POST"])
def edit_product(product_id):
   if not session.get("is_admin"):
       return redirect("/?err=Unauthorised+access")


   db.collection("products").document(product_id).update({
       "price": float(request.form["price"]),
       "desc": request.form["desc"].strip()
   })
   return redirect("/?msg=Product+updated")




# 5. ADMIN ONLY: Delete Product Metadata & Cloud Storage Photo
@app.route("/admin/delete/<product_id>", methods=["POST"])
def delete_product(product_id):
   if not session.get("is_admin"):
       return redirect("/?err=Unauthorised+access")


   doc_ref = db.collection("products").document(product_id)
   doc = doc_ref.get()


   if doc.exists:
       image_url = doc.to_dict().get("image_url", "")
       if image_url:
           blob_name = image_url.split("/")[-1]
           try:
               bucket.blob(blob_name).delete()
           except NotFound:
               pass
       doc_ref.delete()


   return redirect("/?msg=Product+and+image+deleted")




# 6. CUSTOMER CHECKOUT: Stripe Test Card Mode
@app.route("/checkout", methods=["POST"])
def checkout():
   total_amount = float(request.form.get("total", 0))
   if total_amount <= 0:
       return redirect("/")


   checkout_session = stripe.checkout.Session.create(
       payment_method_types=["card"],
       line_items=[{
           "price_data": {
               "currency": "usd",
               "product_data": {"name": "EcoStore Order"},
               "unit_amount": int(round(total_amount * 100)),
           },
           "quantity": 1,
       }],
       mode="payment",
       success_url=request.host_url + "?msg=Payment+successful!+Thank+you+for+your+order.",
       cancel_url=request.host_url,
   )
   return redirect(checkout_session.url, code=303)




if __name__ == "__main__":
   app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
