from flask import Flask, request, jsonify
from flask_cors import CORS
from mistralai import Mistral
import discord
import asyncio
import threading
import json
import os

# ============================================================
#  CONFIG
# ============================================================
DISCORD_TOKEN = "MTQ2ODY3MTA5NTgzNzE2MzY5MQ.GLDjFQ.2iKgBRrHjiw7hQLnu6w7IApr_JgihBT5Xzxb4Q"
MISTRAL_API_KEY = "SGNfRLFbQXlP9I85Iin5CvF7WDLnqK5y"
MODEL = "mistral-small-latest"
# ============================================================

app = Flask(__name__)
CORS(app)
mistral_client = Mistral(api_key=MISTRAL_API_KEY)

SIZES = {
    "small":  {"categories": 5,  "channels": 20,  "roles": 5},
    "medium": {"categories": 10, "channels": 50,  "roles": 15},
    "large":  {"categories": 20, "channels": 100, "roles": 30},
    "mega":   {"categories": 30, "channels": 150, "roles": 50},
}

SEPARATORS = {
    "dot":    "・",
    "bar":    "┃",
    "arrow":  "»",
    "bullet": "•",
    "pipe":   "|",
    "star":   "✦",
    "diamond":"◈",
    "none":   "-",
}

# ── Discord bot (intents minimaux pour API) ──
intents = discord.Intents.default()
discord_client = discord.Client(intents=intents)
bot_loop = asyncio.new_event_loop()
bot_ready = threading.Event()

def run_bot_loop():
    asyncio.set_event_loop(bot_loop)
    bot_loop.run_forever()

threading.Thread(target=run_bot_loop, daemon=True).start()

@discord_client.event
async def on_ready():
    print(f"✅ Discord client prêt : {discord_client.user}")
    bot_ready.set()

async def start_discord():
    await discord_client.start(DISCORD_TOKEN)

asyncio.run_coroutine_threadsafe(start_discord(), bot_loop)

# ── Helpers ──

def generate_structure(theme, language, size):
    nb_cat = size["categories"]
    nb_ch  = size["channels"]
    nb_rol = size["roles"]
    ch_per_cat = max(2, round(nb_ch / nb_cat))

    prompt = f"""Tu es un expert Discord. Génère une structure de serveur pour le thème : "{theme}". Langue : {language}.

OBLIGATOIRE :
- Exactement {nb_cat} catégories
- ~{ch_per_cat} channels par catégorie (total ~{nb_ch}), mix texte et vocal
- Exactement {nb_rol} rôles avec hiérarchie et couleurs hex variées
- 10+ règles

Réponds UNIQUEMENT avec du JSON valide, sans markdown.

{{
  "server_name": "Nom",
  "description": "Description",
  "categories": [
    {{
      "name": "📢 CATÉGORIE",
      "channels": [
        {{"name": "💬 general", "type": "text", "topic": "Description"}},
        {{"name": "🔊 vocal", "type": "voice"}}
      ]
    }}
  ],
  "roles": [
    {{"name": "👑 Fondateur", "color": "#FFD700"}},
    {{"name": "✅ Membre", "color": "#2ECC71"}}
  ],
  "rules": ["Règle 1", "Règle 2"]
}}

Adapte tout au thème "{theme}". JSON UNIQUEMENT."""

    response = mistral_client.chat.complete(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=8000,
        temperature=0.7,
    )
    text = response.choices[0].message.content.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            if part.startswith("json"): text = part[4:]; break
            elif "{" in part: text = part; break
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]
    return json.loads(text)


def get_role_permissions(role_name):
    name = role_name.lower()
    if any(x in name for x in ["fondateur", "owner", "admin", "administrateur"]):
        return discord.Permissions(administrator=True)
    if any(x in name for x in ["modo", "modérateur", "moderator", "staff", "mod"]):
        return discord.Permissions(
            manage_messages=True, kick_members=True, ban_members=True,
            manage_channels=True, mute_members=True, move_members=True,
            read_messages=True, send_messages=True, connect=True, speak=True,
        )
    if any(x in name for x in ["vip", "premium", "boost"]):
        return discord.Permissions(
            read_messages=True, send_messages=True, attach_files=True,
            embed_links=True, add_reactions=True, connect=True, speak=True, stream=True,
        )
    if any(x in name for x in ["nouveau", "new", "visiteur"]):
        return discord.Permissions(read_messages=True, send_messages=True, connect=True)
    return discord.Permissions(
        read_messages=True, send_messages=True, connect=True, speak=True, add_reactions=True,
    )


def format_name(raw, separator):
    raw = raw.strip()
    parts = raw.split(" ", 1)
    if len(parts) == 2:
        first = parts[0]
        if not first.isascii():
            return f"{first}{separator}{parts[1].lower().replace(' ', '-')}"
    return raw.lower().replace(" ", "-")


async def apply_to_guild(guild_id, structure, separator, progress_cb):
    await bot_ready.wait() if not bot_ready.is_set() else None
    guild = discord_client.get_guild(int(guild_id))
    if not guild:
        try:
            guild = await discord_client.fetch_guild(int(guild_id))
        except Exception as e:
            await progress_cb(f"❌ Serveur introuvable : {e}")
            return False

    await progress_cb("🗑️ Suppression des salons...")
    for ch in list(guild.channels):
        try: await ch.delete(); await asyncio.sleep(0.4)
        except: pass

    await progress_cb("🗑️ Suppression des rôles...")
    bot_role_ids = {r.id for r in guild.me.roles}
    for role in guild.roles:
        if role.name == "@everyone" or role.managed or role.id in bot_role_ids or role >= guild.me.top_role:
            continue
        try: await role.delete(); await asyncio.sleep(0.5)
        except: pass

    await progress_cb(f"👥 Création des rôles...")
    created_roles = []
    for rd in structure.get("roles", []):
        try:
            color_hex = rd.get("color", "#99aab5").lstrip("#")
            if len(color_hex) != 6: color_hex = "99aab5"
            color = discord.Color(int(color_hex, 16))
            perms = get_role_permissions(rd["name"])
            role = await guild.create_role(name=rd["name"], color=color, permissions=perms, mentionable=True, hoist=True)
            created_roles.append(role)
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f"[ERR] Rôle: {e}")

    await progress_cb(f"🏗️ Création des catégories et salons...")
    cats = structure.get("categories", [])
    rules_channel = None
    for i, cat in enumerate(cats):
        try:
            cat_name_lower = cat["name"].lower()
            cat_overwrites = {}
            if any(x in cat_name_lower for x in ["staff", "admin", "modération", "privé", "logs"]):
                cat_overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=False)
                for r in created_roles:
                    if any(x in r.name.lower() for x in ["admin", "fondateur", "modo", "staff"]):
                        cat_overwrites[r] = discord.PermissionOverwrite(read_messages=True)
            category = await guild.create_category(cat["name"], overwrites=cat_overwrites)
            await asyncio.sleep(0.5)
            for ch_data in cat.get("channels", []):
                ch_name = format_name(ch_data["name"], separator)
                try:
                    if ch_data.get("type") == "voice":
                        await category.create_voice_channel(name=ch_name)
                    else:
                        ch = await category.create_text_channel(name=ch_name, topic=(ch_data.get("topic") or "")[:1024])
                        if rules_channel is None and any(x in ch_name for x in ["règle", "rule", "accueil", "bienvenue", "welcome"]):
                            rules_channel = ch
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"[ERR] Channel: {e}")
            await progress_cb(f"📊 {i+1}/{len(cats)} catégories créées")
        except Exception as e:
            print(f"[ERR] Catégorie: {e}")

    if rules_channel and structure.get("rules"):
        rules_text = "📋 **RÈGLES**\n\n" + "\n".join(f"**{i+1}.** {r}" for i, r in enumerate(structure["rules"]))
        try: await rules_channel.send(rules_text)
        except: pass

    await progress_cb("✅ Serveur terminé !")
    return True


# ── Routes API ──

@app.route("/api/preview", methods=["POST"])
def preview():
    data = request.json
    theme = data.get("theme", "gaming")
    language = data.get("language", "fr")
    size_key = data.get("size", "medium")
    size = SIZES.get(size_key, SIZES["medium"])
    try:
        structure = generate_structure(theme, language, size)
        return jsonify({"success": True, "structure": structure})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.json
    theme    = data.get("theme", "gaming")
    language = data.get("language", "fr")
    size_key = data.get("size", "medium")
    sep_key  = data.get("separator", "dot")
    guild_id = data.get("guild_id", "")

    if not guild_id:
        return jsonify({"success": False, "error": "ID du serveur manquant"}), 400

    size = SIZES.get(size_key, SIZES["medium"])
    separator = SEPARATORS.get(sep_key, "・")

    try:
        structure = generate_structure(theme, language, size)
    except Exception as e:
        return jsonify({"success": False, "error": f"Erreur Mistral: {e}"}), 500

    logs = []
    async def progress_cb(msg):
        logs.append(msg)
        print(f"[PROGRESS] {msg}")

    future = asyncio.run_coroutine_threadsafe(
        apply_to_guild(guild_id, structure, separator, progress_cb),
        bot_loop
    )
    try:
        result = future.result(timeout=600)
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "logs": logs}), 500

    return jsonify({
        "success": result,
        "server_name": structure.get("server_name"),
        "logs": logs,
        "stats": {
            "categories": len(structure.get("categories", [])),
            "channels": sum(len(c.get("channels", [])) for c in structure.get("categories", [])),
            "roles": len(structure.get("roles", [])),
        }
    })


@app.route("/api/check-bot", methods=["POST"])
def check_bot():
    data = request.json
    guild_id = data.get("guild_id", "")
    if not guild_id:
        return jsonify({"present": False, "error": "ID manquant"}), 400

    async def _check():
        try:
            guild = discord_client.get_guild(int(guild_id))
            if not guild:
                guild = await discord_client.fetch_guild(int(guild_id))
            return {"present": True, "name": guild.name, "members": guild.member_count}
        except Exception as e:
            return {"present": False, "error": str(e)}

    future = asyncio.run_coroutine_threadsafe(_check(), bot_loop)
    try:
        result = future.result(timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({"present": False, "error": str(e)}), 500


if __name__ == "__main__":
    print("🚀 API ArcAI démarrée sur http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
