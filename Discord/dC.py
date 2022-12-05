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

load_dotenv()
token = os.getenv('DISCORD_TOKEN')
hostName = os.getenv('HOST_NAME')
serverPort = os.getenv('PORT')
NLChannelID = int(os.getenv('NEOS_LINK_CHANNEL_ID'))
serverID = os.getenv('SERVER_ID')
loggingDir = os.getenv('LOG_DIR')
fileDir = os.getenv('FILE_DIR')
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')

dLogger = logging.getLogger('discord')
dLogger.setLevel(logging.INFO)
dHandler = logging.handlers.RotatingFileHandler(
    filename=f'{loggingDir}/discord.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate through 5 files
)
dHandler.setFormatter(formatter)
dLogger.addHandler(dHandler)

wsLogger = logging.getLogger('websockets.server')
wsLogger.setLevel(logging.INFO)
wsHandler = logging.handlers.RotatingFileHandler(
    filename=f'{loggingDir}/web_socket.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate through 5 files
)
wsHandler.setFormatter(formatter)
wsLogger.addHandler(wsHandler)

dcLogger = logging.getLogger('dC')
dcLogger.setLevel(logging.INFO)
dcHandler = logging.handlers.RotatingFileHandler(
    filename=f'{loggingDir}/dC.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate through 5 files
)
dcHandler.setFormatter(formatter)
dcLogger.addHandler(dcHandler)

NLChannel = discord.Object(id=NLChannelID, type=discord.TextChannel)  #
knownPeople = {}
discordServer = discord.Object(id=serverID)
dIntents = discord.Intents.all()


# client = discord.Client(intents=intents)
class DClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=discordServer)
        await self.tree.sync(guild=discordServer)


client = DClient(intents=dIntents)
messageReceived = False
clients = {}

helpMessage = ''' 
__NeosVR Link__
/NL Connect - Changes the Link Channel
/NL Link <Neos UserID> - adds the user to the known players list.
'''


@client.event
async def on_ready():
    print(f"Bot has logged in as {client.user}")
    dcLogger.info(f"Bot has logged in as {client.user}")


@client.event
async def on_message(message):
    if message.author == client.user:
        dcLogger.debug(f"Bot sent the message : {message.content}")
        return
    if message.channel.id == NLChannelID:
        attachments = message.attachments
        content = await formatMessage(message, "")
        for connectedClient in clients.values():
            if len(attachments) != 0:
                try:
                    await send(connectedClient, f"¦a{attachments[0].proxy_url}")
                    content = "+ " + content
                except IndexError:
                    pass
            await send(connectedClient, content)


@client.tree.command(name="neos_link", description="Command to Link your discord account to a Neos Username.")
@app_commands.describe(username='Your NeosVR Username to link to.')
async def link(interaction: discord.Interaction, username: str):
    await addUserToList(interaction, username)
    await interaction.response.send_message(f"Added {interaction.user} to the known players list.")


@client.tree.command(name="change_channel", description="Command to change the channel that the NeosVR bot talks in.")
async def changeChannel(interaction: discord.Interaction):
    global NLChannelID
    await sendMessage(NLChannelID, f"Changed Neos Link Channel to {interaction.channel.name}")
    NLChannelID = interaction.channel.id
    dcLogger.info(f"Changing channel to {interaction.channel.name}")
    await interaction.response.send_message(f"Changed Neos Link Channel to {interaction.channel.name}")


@client.event
async def sendMessage(channelID, message):
    channel = client.get_channel(channelID)
    try:
        dcLogger.debug(f"Bot Sending {message} to {channel.name}")
        await channel.send(message)
    except discord.errors.HTTPException:
        await channel.send(message[0:4000])


@client.event
async def fetchMentionedUser(name, discriminator):
    user = discord.utils.get(client.users, name=name, discriminator=discriminator)
    return user


@client.event
async def fetchMessages(channelID, amount):
    history = ""
    channel = client.get_channel(channelID)
    dcLogger.debug(f"Bot fetching history for {channel.name}")
    async for message in channel.history(limit=amount):
        formatted = await formatMessage(message, "¦")
        history = f"\n{formatted}{history}"
    return history


async def addUserToList(interaction, username):
    global knownPeople
    dcLogger.info(f"Adding {interaction.user} : {username} to Known Players List.")
    if interaction.user.nick is not None:
        knownPeople[username] = interaction.user.nick
    else:
        knownPeople[username] = interaction.user.name
    with open(f"{fileDir}/knownUsers.dict", 'w') as file:
        file.write(str(knownPeople))


async def formatMessage(message, inital):
    status = {"online": "On", "idle": "Idle", "dnd": "DnD", "offline": "off"}
    content = str(message.content)
    content = content.replace("¦", "|")
    content = await stripRtf(content)
    if message.author.nick is not None:
        author = str(message.author.nick)
    else:
        author = str(message.author.name)
    authorOnlineStatus = str(message.author.status)
    if str(message.author) == "Da Best Bot#9808":
        formatted = f"{inital}{content}"
    else:
        formatted = f"{inital}{author} ({status[authorOnlineStatus]}) - {content}"
    return formatted


async def splitMessage(message):
    messageDict = {}
    messageList = message.split(",")
    messageDict["UserID"] = messageList[0]
    messageDict["WorldStatus"] = messageList[1]
    messageDict["Message"] = messageList[2]
    return messageDict


async def stripRtf(text):
    found = False
    rftTags = ["b", "i", "u", "s", "sup", "sub", "color", "colour", "size", "noparse", "lowercase", "uppercase", "mark",
               "br", "nobr", "spritename"]
    rtf = re.findall("<.*?>", text)
    for tag in rtf:
        for tags in rftTags:
            if tag.find(tags):
                found = True
    if found is True:
        text = re.sub("<.*?>", "", text)
    return text


async def findMentionedUser(message):
    if "@" in message:
        try:
            before = message.split("@")
            after = before[1].split("#")
            name = after[0]
            discriminator = after[1][0:4]
            user = await fetchMentionedUser(name, discriminator)
            message = before[0] + user.mention + after[1][4:-1]
        except IndexError:
            dcLogger.info(f"Could not find user in {message}")
    return message


async def createMessage(messageDict):
    messageDict['Message'] = await findMentionedUser(messageDict['Message'])
    worldType = ["Pr", "L", "C", "C+", "R", "P", "H"]
    message = f"{knownPeople[messageDict['UserID']]} ({worldType[int(messageDict['WorldStatus'])]}) - {messageDict['Message'].replace('¦', '|')}"
    return message


async def getDiscordUN(messageDict):
    mOwner = messageDict["UserID"]
    if mOwner in knownPeople:
        discordUN = knownPeople[mOwner]
    else:
        discordUN = None
    return discordUN


async def sendMessages(websocket):
    name = websocket.id
    connected = websocket
    clients[name] = connected
    history = await fetchMessages(NLChannelID, 15)
    for connectedClient in clients.values():
        message = history
        dcLogger.info(f"WS Sending history to {str(connectedClient)}")
        asyncio.create_task(send(connectedClient, message))


async def send(websocket, message):
    try:
        await websocket.send(message)
    except ws.ConnectionClosed:
        pass


async def wsMain(websocket):
    await client.wait_until_ready()
    try:
        await sendMessages(websocket)
        async for message in websocket:
            messageDict = await splitMessage(message)
            dUsername = await getDiscordUN(messageDict)
            if dUsername is None:
                await asyncio.sleep(0.1)
                dcLogger.warning(f"WS Unverified User {messageDict['UserID']}")
                await websocket.send("¦UnVerified")
            else:
                sMessage = await createMessage(messageDict)
                await sendMessage(channelID=NLChannelID, message=sMessage)
                await websocket.send(sMessage)
    except websockets.exceptions.ConnectionClosedError:
        popList = []
        for aClient in clients:
            try:
                await clients[aClient].ping(data=None)
            except websockets.exceptions.ConnectionClosedError:
                popList.append(aClient)
                dcLogger.warning(f"WS {aClient} Disconnected")
        for i in popList:
            clients.pop(i)


async def wsStart():
    async with ws.serve(wsMain, hostName, serverPort):
        dcLogger.info(f"WS Started")
        await asyncio.Future()  # run forever


async def presenceChange():
    await asyncio.sleep(360)
    oldClients = 0
    while True:
        if len(clients) == 1:
            game = discord.Game(f"with {len(clients)} client.")
            await client.change_presence(status=discord.Status.online, activity=game)
        elif len(clients) == 0:
            if oldClients == 0:
                game = discord.Game(f"with {len(clients)} clients.")
                await client.change_presence(status=discord.Status.idle, activity=game)
            oldClients = oldClients + 1
            if oldClients == 10:
                await asyncio.sleep(120)
                oldClients = 0
        else:
            game = discord.Game(f"with {len(clients)} clients.")
            await client.change_presence(status=discord.Status.online, activity=game)
        await asyncio.sleep(60)


async def start():
    global knownPeople
    dcLogger.info(f"Fetching Known Users")
    with open(f"{fileDir}/knownUsers.dict", 'r') as file:
        fileContent = file.read()
        fileContent = fileContent.replace("'", '"')
        dcLogger.debug(fileContent)
        knownPeople = json.loads(fileContent)
    await asyncio.gather(client.start(token), wsStart(), presenceChange())


asyncio.run(start())
