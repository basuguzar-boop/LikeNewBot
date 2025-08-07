import json
import os
import random
from uuid import uuid4
from datetime import datetime
from io import BytesIO
import pandas as pd
import re

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InputFile
)
from telegram.ext import (
    Updater,
    InlineQueryHandler,
    CallbackQueryHandler,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    Filters
)

from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
BOT_USERNAME = os.getenv("BOT_USERNAME")
OWNER_USERNAMES = os.getenv("OWNER_USERNAMES", "").split(",")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
CONTACTS_FILE = os.getenv("CONTACTS_FILE", "contacts.json")
IMAGES_FILE = os.getenv("IMAGES_FILE", "images")


# --- Permissions ---
def owner_only(func):
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user = update.effective_user
        if user.username not in OWNER_USERNAMES:
            update.message.reply_text("\u274c You are not allowed to use this command.")
            return
        return func(update, context, *args, **kwargs)
    return wrapper

# --- Load/save data ---
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

likes = load_data()

# --- Helpers ---
def generate_unique_post_id():
    while True:
        post_id = str(random.randint(100000, 999999))
        if post_id not in likes:
            return post_id

def safe_str(s):
    if not isinstance(s, str):
        s = str(s)
    # Remove surrogate characters and non-encodable bytes
    s = s.encode("utf-8", "surrogatepass").decode("utf-8", "ignore")
    s = ''.join(c for c in s if not (0xD800 <= ord(c) <= 0xDFFF))
    return s


def format_datetime(dt_string):
    try:
        dt = datetime.fromisoformat(dt_string)
        date_str = dt.strftime("%Y/%m/%d")
        time_str = dt.strftime("%H-%M-%S") + f"-{int(dt.microsecond / 1000):03d}"
        return date_str, time_str
    except:
        return "-", "-"

# --- Contact Map (persistent phone numbers) ---
def load_contacts():
    if not os.path.exists(CONTACTS_FILE):
        return {}
    try:
        with open(CONTACTS_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_contacts(contact_map):
    with open(CONTACTS_FILE, 'w') as f:
        json.dump(contact_map, f, indent=4)

contact_map = load_contacts()

def upload(update: Update, context: CallbackContext):
    update.message.reply_text("Please upload the image you want to associate with a voting post. The bot will give you a 6-digit ID for the image.")


# --- Command Handlers ---
# --- Fixed start() ---
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    is_owner = user.username in OWNER_USERNAMES

    escaped_bot_username = BOT_USERNAME.replace("_", "\\_")
    escaped_channel = CHANNEL_USERNAME.replace("_", "\\_")

    if is_owner:
        text = (
            "\U0001F44B Welcome\\!\n\n"
            f"Type \\@{escaped_bot_username} ❤️ Like in any chat to create a likeable post\\.\n"
            f"The message is forwardable and users must subscribe to {escaped_channel} to vote\\. ✅"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u2795 Create Voting Post", switch_inline_query="❤️ Like")]
        ])
        update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=markup)
    else:
        text = (
            "\U0001F44B Welcome\\!\n\n"
            "Only post creators can make likeable messages\\.\n\n"
            "To share an existing post, use:\n"
            "`/idpost <6\\-digit post ID>`\n"
            "Example: `/idpost 123456`"
        )
        update.message.reply_text(text, parse_mode="MarkdownV2")

# --- Error Handler ---
def error_handler(update: object, context: CallbackContext):
    print(f"⚠️ Error: {context.error}")
    if update and hasattr(update, "message") and update.message:
        try:
            update.message.reply_text("⚠️ An unexpected error occurred.")
        except Exception:
            pass  # Fail silently to avoid infinite loops


@owner_only
def upload_image(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Please upload the image you want to associate with a voting post. The bot will give you a 6-digit ID for the image."
    )


def idpost(update: Update, context: CallbackContext):
    args = context.args
    if not args or not args[0].isdigit() or len(args[0]) != 6:
        update.message.reply_text("\u274c Usage: /idpost <6-digit post ID>\nExample: /idpost 123456",
                                  parse_mode="Markdown")
        return
    post_id = args[0]
    post_data = likes.get(post_id)

    if not post_data:
        update.message.reply_text("❌ Post not found.")
        return

    message = post_data.get("message", "")
    image_path = post_data.get("image_path", None)

    # Check if the post has a message and image
    text = f"✅ Post:\n\n{message}\n\n🔁 Share it:\n@{BOT_USERNAME} {post_id}"
    if image_path:
        with open(image_path, 'rb') as img:
            update.message.reply_photo(photo=img, caption=text)
    else:
        update.message.reply_text(text)


def share_contact(update: Update, context: CallbackContext):
    button = KeyboardButton("\ud83d\udcde Share Contact", request_contact=True)
    markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
    update.message.reply_text("Please share your contact:", reply_markup=markup)

def handle_contact(update: Update, context: CallbackContext):
    user = update.effective_user
    phone = update.message.contact.phone_number
    contact_map[str(user.id)] = phone
    save_contacts(contact_map)
    for post in likes.values():
        for voter in post.get("voters", []):
            if voter["id"] == user.id:
                voter["phone"] = phone
    save_data(likes)
    update.message.reply_text("\ud83d\udcde Contact saved!")

@owner_only
def handle_image(update: Update, context: CallbackContext):
    if update.message.photo:
        # Get the largest photo
        file = update.message.photo[-1].get_file()
        file_id = str(random.randint(100000, 999999))  # Generate a random 6-digit ID
        file_path = os.path.join(IMAGES_FILE, f"{file_id}.jpg")

        # Download and save the image
        file.download(file_path)

        # Respond with the generated ID
        update.message.reply_text(f"✅ Image uploaded successfully! Your image ID: {file_id}")

        # Save image info (ID and path)
        image_data = load_data()
        image_data[file_id] = {"image_path": file_path}
        save_data(image_data)
    else:
        update.message.reply_text("❌ Please send a valid image.")

@owner_only
def image_upload_command(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Upload an image by sending it to the bot, and it will be assigned a unique 6-digit ID."
    )



@owner_only
def stats(update: Update, context: CallbackContext):
    total_posts = len(likes)
    total_votes = sum(len(p.get('voters', [])) for p in likes.values())
    all_ids = [v['id'] for p in likes.values() for v in p.get('voters', [])]
    unique_voters = len(set(all_ids))
    update.message.reply_text(f"\ud83d\udcca Total Posts: {total_posts}\n\ud83d\udc65 Unique Voters: {unique_voters}\n\u2764\ufe0f Total Votes: {total_votes}")

@owner_only
def totalstats(update: Update, context: CallbackContext):
    rows = []
    for post_id, post_data in likes.items():
        created_date, created_time = format_datetime(post_data.get("created_at", "-"))
        total_likes = len(post_data.get("voters", []))

        for voter in post_data.get("voters", []):
            voted_date, voted_time = format_datetime(voter.get("voted_at", "-"))
            row = {
                "Post ID": safe_str(post_id),
                "Voter Name": safe_str(voter.get("name", "-")),
                "Username": safe_str(voter.get("username", "-")),
                "Phone": safe_str(voter.get("phone", "-")),
                "Post Created Date": created_date,
                "Post Created Time": created_time,
                "Voted Date": voted_date,
                "Voted Time": voted_time,
                "Post Total Likes": total_likes
            }
            rows.append(row)

    if not rows:
        update.message.reply_text("No data available.")
        return

    df = pd.DataFrame(rows)

    # Clean all cells
    df = df.applymap(safe_str)

    try:
        excel_stream = BytesIO()
        df.to_excel(excel_stream, index=False, engine='openpyxl')
        excel_stream.seek(0)
        update.message.reply_document(
            document=InputFile(excel_stream, filename="total_stats.xlsx"),
            filename="total_stats.xlsx",
            caption="📊 Total Stats Report"
        )
    except Exception as e:
        update.message.reply_text(f"❌ Failed to export: {e}")


# --- Inline Queries ---
@owner_only
def handle_inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    user = update.inline_query.from_user

    # If it's a valid post ID (6-digit), allow reuse of existing posts with images
    if query.isdigit() and len(query) == 6 and query in likes:
        emoji = likes[query].get("emoji", "❤️")
        count = len(likes[query].get("voters", []))
        message = likes[query].get("message", "")
        image_id = query  # Use the ID to fetch image data

        text = f"{message}\n\n🔥 Like this post below!\n🔔 Post ID: {query}"

        # Buttons for the post
        buttons = [
            [InlineKeyboardButton(f"{emoji} ({count})", url=f"https://t.me/{CHANNEL_USERNAME.strip('@')}")],
            [InlineKeyboardButton("📢 Visit Channel", url=f"https://t.me/{CHANNEL_USERNAME.strip('@')}")],
            [InlineKeyboardButton("📤 Share with Your Friend", switch_inline_query=query)]
        ]

        result = InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"Post ID: {query}",
            input_message_content=InputTextMessageContent(text),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        update.inline_query.answer([result], cache_time=0)
        return

    if not user.username in OWNER_USERNAMES:
        return

    # Extract emoji, message, and image ID
    match = re.search(r'([^\s]+)\s+Like(?:\s+"([^"]+)"|\s+(.*))?\s*(\d{6})?', query)
    if match:
        emoji = match.group(1)
        message = match.group(2) or match.group(3)
        image_id = match.group(4)
    else:
        emoji = "❤️"
        message = None
        image_id = None

    post_id = generate_unique_post_id()
    text = f"{message}\n\n🔥 Like this post below!\n🔔 Post ID: {post_id}" if message else f"🔥 Like this post below!\n🔔 Post ID: {post_id}"

    likes[post_id] = {
        "emoji": emoji,
        "message": message or "",
        "created_at": datetime.now().isoformat(),
        "voters": [],
        "image_path": os.path.join(IMAGE_FOLDER, f"{image_id}.jpg") if image_id else None
    }
    save_data(likes)

    buttons = [
        [InlineKeyboardButton(f"{emoji} (0)", callback_data=f"vote|{post_id}|{emoji}")],
        [InlineKeyboardButton("📢 Visit Channel", url=f"https://t.me/{CHANNEL_USERNAME.strip('@')}")],
        [InlineKeyboardButton("📤 Share with Your Friend", switch_inline_query=post_id)]
    ]

    result = InlineQueryResultArticle(
        id=str(uuid4()),
        title="Create Voting Post",
        input_message_content=InputTextMessageContent(text),
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    update.inline_query.answer([result], cache_time=0)


# --- Voting Handler ---
def handle_vote(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    data = query.data
    inline_msg_id = query.inline_message_id
    try:
        _, post_id, emoji = data.split("|")
    except:
        query.answer("Invalid vote data.")
        return
    if post_id not in likes:
        query.answer("Post not found.")
        return
    existing = [v['id'] for v in likes[post_id]['voters']]
    if user.id in existing:
        query.answer("✅ You already voted.")
        return
    contact_map = load_contacts()  # Reload from contacts.json

    likes[post_id]['voters'].append({
        "id": user.id,
        "name": user.full_name,
        "username": user.username or "",
        "phone": contact_map.get(str(user.id), "-"),
        "voted_at": datetime.now().isoformat()
    })
    save_data(likes)
    count = len(likes[post_id]['voters'])
    new_button = InlineKeyboardButton(f"{emoji} ({count})", callback_data=f"vote|{post_id}|{emoji}")
    markup = InlineKeyboardMarkup([
        [new_button],
        [InlineKeyboardButton("📢 Visit Channel", url=f"https://t.me/{CHANNEL_USERNAME.strip('@')}")],
        [InlineKeyboardButton("📤 Share with Your Friend", switch_inline_query=post_id)]
    ])
    context.bot.edit_message_reply_markup(
        inline_message_id=inline_msg_id,
        reply_markup=markup
    )
    query.answer("✅ Vote counted!")

# --- Main ---
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # Public commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("idpost", idpost))
    dp.add_handler(CommandHandler("sharecontact", share_contact))
    dp.add_handler(MessageHandler(Filters.contact, handle_contact))
    dp.add_handler(CommandHandler("upload", upload))  # Upload command
    dp.add_handler(MessageHandler(Filters.photo, handle_image))  # Handle image upload

    # Owner-only
    dp.add_handler(CommandHandler("stats", stats))
    dp.add_handler(CommandHandler("totalstats", totalstats))

    # Inline and voting
    dp.add_handler(InlineQueryHandler(handle_inline_query))
    dp.add_handler(CallbackQueryHandler(handle_vote))

    updater.start_polling()
    print("✅ Bot is running...")
    updater.idle()

if __name__ == '__main__':
    main()