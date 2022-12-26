from dotenv import load_dotenv
import os
import discord
import asyncio
import websockets as ws
import logging
import re
import json
import websockets.exceptions
from discord import app_commands
import logging.handlers
import aiohttp

load_dotenv()
token = os.getenv('DISCORD_TOKEN')
host_name = os.getenv('HOST_NAME')
server_port = os.getenv('PORT')
nl_channel_id = int(os.getenv('NEOS_LINK_CHANNEL_ID'))
server_id = os.getenv('SERVER_ID')
logging_dir = os.getenv('LOG_DIR')
file_dir = os.getenv('FILE_DIR')
webhook_url = os.getenv('WEBHOOK_URL')
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')

discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.INFO)
discord_handler = logging.handlers.RotatingFileHandler(
    filename=f'{logging_dir}/discord.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate through 5 files
)
discord_handler.setFormatter(formatter)
discord_logger.addHandler(discord_handler)

websockets_logger = logging.getLogger('websockets.server')
websockets_logger.setLevel(logging.INFO)
websockets_handler = logging.handlers.RotatingFileHandler(
    filename=f'{logging_dir}/web_socket.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate through 5 files
)
websockets_handler.setFormatter(formatter)
websockets_logger.addHandler(websockets_handler)

discord_link_logger = logging.getLogger('discord_link')
discord_link_logger.setLevel(logging.INFO)
discord_link_handler = logging.handlers.RotatingFileHandler(
    filename=f'{logging_dir}/discord_link.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate through 5 files
)
discord_link_handler.setFormatter(formatter)
discord_link_logger.addHandler(discord_link_handler)

neos_link_channel = discord.Object(id=nl_channel_id, type=discord.TextChannel)
registered_users = {}
discord_server = discord.Object(id=server_id)
discord_intents = discord.Intents.all()


class DiscordClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=discord_server)
        await self.tree.sync(guild=discord_server)


client = DiscordClient(intents=discord_intents)
message_received = False
clients = {}

help_message = ''' 
__NeosVR Link__
/NL Connect - Changes the Link Channel
/NL Link <Neos UserID> - adds the user to the known players list.
'''


@client.event
async def on_ready():
    print(f"Bot has logged in as {client.user}")
    discord_link_logger.info(f"Bot has logged in as {client.user}")


@client.event
async def on_message(message):
    if message.author == client.user:
        discord_link_logger.debug(f"Bot sent the message : {message.content}")
        return
    if message.channel.id == nl_channel_id:
        attachments = message.attachments
        content = await format_message(message, "")
        for connected_client in clients.values():
            if len(attachments) != 0:
                try:
                    await send_websocket_message(connected_client, f"¦a{attachments[0].proxy_url}")
                    content = "+ " + content
                except IndexError:
                    pass
            await send_websocket_message(connected_client, content)


@client.tree.command(name="neos_link", description="Command to Link your discord account to a Neos Username.")
@app_commands.describe(username='Your NeosVR Username to link to.')
async def link(interaction: discord.Interaction, username: str):
    if username.startswith("U-"):
        await add_user_to_list(interaction, username)
        await interaction.response.send_message(f"Added {interaction.user} to the known players list.")
    else:
        await interaction.response.send_message(
            f"Did not Add {interaction.user} : {username} to registered players due to incorrect formatting. \n Needs to start with U-")
        discord_link_logger.info(
            f"Did not Add {interaction.user} : {username} to registered players due to incorrect formatting.")


@client.tree.command(name="change_channel", description="Command to change the channel that the NeosVR bot talks in.")
async def change_channel(interaction: discord.Interaction):
    global nl_channel_id
    try:
        webhooks = await interaction.channel.webhooks()
        for i in webhooks:
            if i.name == "NeosVR Link":
                global webhook_url
                webhook_url = i.url()
            else:
                discord_link_logger.info(f"{i.name} is not named NeosVR Link / is not the webhook we want")
    except discord.Forbidden:
        discord_link_logger.error("Don't have manage webhook permission")
    await send_message(nl_channel_id, f"Changed Neos Link Channel to {interaction.channel.name}")
    nl_channel_id = interaction.channel.id
    discord_link_logger.info(f"Changing channel to {interaction.channel.name}")
    await interaction.response.send_message(f"Changed Neos Link Channel to {interaction.channel.name}")


@client.event
async def send_message(channel_id, message):
    channel = client.get_channel(channel_id)
    try:
        discord_link_logger.debug(f"Bot Sending {message} to {channel.name}")
        await channel.send(message)
    except discord.errors.HTTPException:
        await channel.send(message[0:4000])


@client.event
async def fetch_mentioned_user(name, discriminator):
    user = discord.utils.get(client.users, name=name, discriminator=discriminator)
    return user


@client.event
async def fetch_messages(channel_id, amount):
    history = ""
    channel = client.get_channel(channel_id)
    discord_link_logger.debug(f"Bot fetching history for {channel.name}")
    async for message in channel.history(limit=amount):
        formatted = await format_message(message, "¦")
        history = f"\n{formatted}{history}"
    return history


async def send_webhook_message(message_dictionary):
    username, avatar_url, message = await create_webhook_message(message_dictionary)
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(webhook_url, session=session)
        await webhook.send(message, username=username, avatar_url=avatar_url)


async def add_user_to_list(interaction, username):
    global registered_users
    user = {}
    discord_link_logger.info(f"Adding {interaction.user} : {username} to Known Players List.")
    user["id"] = interaction.user.id
    if interaction.user.nick is not None:
        user["discord_username"] = interaction.user.nick
        # knownPeople[username] = interaction.user.nick
    else:
        user["discord_username"] = interaction.user.name
        # knownPeople[username] = interaction.user.name
    user["avatar_url"] = interaction.user.avatar.url
    registered_users[username] = user
    with open(f"{file_dir}/registered_users.json", 'w') as file:
        file.write(json.dumps(registered_users, indent=4))


async def format_message(message, initial):
    status = {"online": "On", "idle": "Idle", "dnd": "DnD", "offline": "off"}
    content = str(message.content)
    content = content.replace("¦", "|")
    content = await strip_rtf(content)
    try:
        if message.author.nick is not None:
            author = str(message.author.nick)
        else:
            author = str(message.author.name)
        author_online_status = str(message.author.status)
        if str(message.author) == "Da Best Bot#9808":
            formatted = f"{initial}{content}"
        else:
            formatted = f"{initial}{author} ({status[author_online_status]}) - {content}"
    except AttributeError:
        author = str(message.author.name)
        formatted = f"{initial}{author} - {content}"

    return formatted


async def split_message(message):
    message_dict = {}
    message_list = message.split(",")
    message_dict["user_id"] = message_list[0]
    message_dict["world_status"] = message_list[1]
    message_dict["message"] = message_list[2]
    return message_dict


async def strip_rtf(text):
    found = False
    rft_tags = ["b", "i", "u", "s", "sup", "sub", "color", "colour", "size", "noparse", "lowercase", "uppercase",
                "mark", "br", "nobr", "spritename"]
    rtf = re.findall("<.*?>", text)
    for tag in rtf:
        for tags in rft_tags:
            if tag.find(tags):
                found = True
    if found is True:
        text = re.sub("<.*?>", "", text)
    return text


async def find_mentioned_user(message):
    if "@" in message:
        try:
            before = message.split("@")
            after = before[1].split("#")
            name = after[0]
            discriminator = after[1][0:4]
            user = await fetch_mentioned_user(name, discriminator)
            message = before[0] + user.mention + after[1][4:-1]
        except IndexError:
            discord_link_logger.info(f"Could not find user in {message}")
    return message


async def create_message(message_dictionary):
    message_dictionary['message'] = await find_mentioned_user(message_dictionary['message'])
    world_type = ["Pr", "L", "C", "C+", "R", "P", "H"]
    message = f"{registered_users[message_dictionary['user_id']]['discord_username']} ({world_type[int(message_dictionary['world_status'])]}) - {message_dictionary['message'].replace('¦', '|')} "
    return message


async def create_webhook_message(message_dictionary):
    message_dictionary['message'] = await find_mentioned_user(message_dictionary['message'])
    world_type = ["Pr", "L", "C", "C+", "R", "P", "H"]
    message = message_dictionary["message"]
    username = registered_users[message_dictionary['user_id']]['discord_username'] + " (" + world_type[
        int(message_dictionary['world_status'])] + ")"
    avatar_url = registered_users[message_dictionary['user_id']]['avatar_url']
    return username, avatar_url, message


async def get_discord_user(message_dictionary):
    message_owner = message_dictionary["user_id"]
    if message_owner in registered_users:
        discord_user = registered_users[message_owner]
    else:
        discord_user = None
    return discord_user


async def send_history(websocket):
    name = websocket.id
    connected = websocket
    clients[name] = connected
    history = await fetch_messages(nl_channel_id, 15)
    for connected_client in clients.values():
        message = history
        discord_link_logger.debug(f"WS Sending history to {str(connected_client)}")
        asyncio.create_task(send_websocket_message(connected_client, message))


async def send_websocket_message(websocket, message):
    try:
        await websocket.send(message)
    except ws.ConnectionClosed:
        pass


async def websocket_main(websocket):
    await client.wait_until_ready()
    try:
        await send_history(websocket)
        async for message in websocket:
            message_dictionary = await split_message(message)
            discord_username = await get_discord_user(message_dictionary)
            if discord_username is None:
                await asyncio.sleep(0.1)
                discord_link_logger.warning(f"WS Unverified User {message_dictionary['user_id']}")
                await websocket.send("¦UnVerified")
            else:
                await send_webhook_message(message_dictionary)
                # sent_message = await create_message(message_dictionary)
                # await send_message(channel_id=nl_channel_id, message=sent_message)
                # await websocket.send(sent_message)

    except websockets.exceptions.ConnectionClosedError:
        pop_list = []
        for a_client in clients:
            try:
                await clients[a_client].ping(data=None)
            except websockets.exceptions.ConnectionClosedError:
                pop_list.append(a_client)
                discord_link_logger.warning(f"WS {a_client} Disconnected")
        for i in pop_list:
            clients.pop(i)


async def websocket_start():
    async with ws.serve(websocket_main, host_name, server_port):
        discord_link_logger.info(f"WS Started")
        await asyncio.Future()  # run forever


async def presenceChange():
    await asyncio.sleep(360)
    old_clients = 0
    while True:
        if len(clients) == 1:
            game = discord.Game(f"with {len(clients)} client.")
            await client.change_presence(status=discord.Status.online, activity=game)
        elif len(clients) == 0:
            if old_clients == 0:
                game = discord.Game(f"with {len(clients)} clients.")
                await client.change_presence(status=discord.Status.idle, activity=game)
            old_clients = old_clients + 1
            if old_clients == 10:
                await asyncio.sleep(120)
                old_clients = 0
        else:
            game = discord.Game(f"with {len(clients)} clients.")
            await client.change_presence(status=discord.Status.online, activity=game)
        await asyncio.sleep(60)


async def start():
    global registered_users
    discord_link_logger.info(f"Fetching Known Users")
    try:
        try:
            with open(f"{file_dir}/registered_users.json", 'r') as file:
                file_content = file.read()
                discord_link_logger.debug(file_content)
                registered_users = json.loads(file_content)
        except FileNotFoundError:
            discord_link_logger.error(f"{file_dir}/registered_users.json not found.")
    except json.decoder.JSONDecodeError:
        discord_link_logger.error(f"{file_dir}/registered_users.json not formatted like JSON.")
    await asyncio.gather(client.start(token), websocket_start(), presenceChange())


asyncio.run(start())
