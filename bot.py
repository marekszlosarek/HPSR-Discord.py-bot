import os
from datetime import datetime, timezone, UTC
from dotenv import load_dotenv
from random import randint
import tracemalloc
import discord
from discord.ext import commands, tasks
import speedruncompy
from speedruncompy import VarValues
import json
from utilis.verify_settings import *
from utilis.pace_graph_generator import get_graph
import aiohttp
import websockets
import asyncio


tracemalloc.start()
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
verifySettings('settings.json')
with open('settings.json', 'r') as settingsFile:
    settings = json.load(settingsFile) 

class Run:
    def __init__(self, runData, categoryData, gameData, levelData, valuesData, variablesData, playersData) -> None:
        self.id = runData.id
        self.position = runData.place
        self.time = runData.time if runData.time is not None else (
            runData.timeWithLoads if runData.timeWithLoads is not None else runData.igt
        )
        self.dateSubmitted = runData.dateSubmitted
        self.playerIds = runData.playerIds

        self.gameInfo = {
            'id': gameData.id,
            'name': gameData.name,
            'abbreviation': gameData.url,
            'cover': '',
        }

        for asset in gameData.staticAssets:
            if asset.assetType == 'cover':
                self.gameInfo['cover'] = 'https://www.speedrun.com' + asset.path

        self.settings = settings['games'].get(
            self.gameInfo['id'],
            settings['games']['default']
        )

        self.categoryInfo = {
            'id': categoryData.id,
            'name': categoryData.name,
        }

        self.isFullGame = levelData is None
        if not self.isFullGame:
            self.levelInfo = {
                'id': levelData.id,
                'name': levelData.name,
            }

        self.subcategories = {
            variableData.id: variableData.isSubcategory
            for variableData in variablesData
        }

        self.valuesInfo = [
            {
                'id': valueData.id,
                'name': valueData.name,
                'variableId': valueData.variableId,
            }
            for valueData in valuesData
            if self.subcategories.get(valueData.variableId, False)
        ]

        self.playersInfo = [
            {
                'id': playerData.id,
                'name': playerData.name,
                'url': (
                    f'https://www.speedrun.com/users/{playerData.url}'
                    if playerData.url else None
                ),
                'image': (
                    'https://www.speedrun.com' + [asset for asset in playerData.staticAssets if asset.assetType == 'image'][0].path
                    if playerData.staticAssets else None
                ),
                'country': (
                    playerData.areaId.split('/')[0]
                    if playerData.areaId else None
                ),
            }
            for playerData in playersData
        ]

        self.weblink = (
            f'https://www.speedrun.com/{self.gameInfo["abbreviation"]}'
            f'/runs/{self.id}'
        )

        level_prefix = (
            f'l_{self.levelInfo["id"]}-'
            if not self.isFullGame else ''
        )
        values_suffix = ''.join(
            f'-{value["variableId"]}.{value["id"]}'
            for value in self.valuesInfo
        )
        self.leaderboardLink = (
            f'https://www.speedrun.com/{self.gameInfo["abbreviation"]}'
            f'?x={level_prefix}{self.categoryInfo["id"]}{values_suffix}'
        )

        if self.position == 1 and (
            self.isFullGame or
            (not self.isFullGame and self.settings['il_mode'] > 1)
        ):
            self.pings = ' '.join(self.settings['wr_ping'])
        else:
            self.pings = ''

    def leaderboardParams(self) -> dict:
        params = {
            'gameId': self.gameInfo['id'],
            'categoryId': self.categoryInfo['id'],
            'obsolete': 1,
            'values': [
                VarValues(
                    variableId=value['variableId'],
                    valueIds=[value['id']]
                )
                for value in self.valuesInfo
                if self.subcategories.get(value['variableId'], False)
            ],
        }

        if not self.isFullGame:
            params['levelId'] = self.levelInfo['id']

        return params

    async def setPreviousPB(self) -> None:
        response = await speedruncompy.GetGameLeaderboard2(
            **self.leaderboardParams()
        ).perform_all()

        oldPB = {
            'exists': False,
            'place': 1,
            'time': 0,
        }

        for run in response.runList:
            if run.obsolete:
                if run.playerIds == self.playerIds:
                    oldPB['exists'] = True
                    oldPB['time'] = (
                        run.time
                        if run.time is not None
                        else (
                            run.timeWithLoads
                            if run.timeWithLoads is not None
                            else run.igt
                        )
                    )
                    self.oldPB = oldPB
                    return
            elif run.place is not None:
                oldPB['place'] = run.place

        self.oldPB = oldPB


class RunEmbed(discord.Embed):
    def __init__(self, run: Run) -> None:
        self.run = run
        colour = self.run.settings['colour']
        description = self.runDescription()
        timestamp = datetime.fromtimestamp(self.run.dateSubmitted, timezone.utc)
        super().__init__(colour=colour, description=description, timestamp=timestamp)

        self.set_author(
            name = self.categoryDisplay(),
            url = self.run.leaderboardLink,
            icon_url = run.playersInfo[0]['image'] if len(run.playersInfo)==1 else None
        )
        self.set_thumbnail(url=self.run.gameInfo['cover'].replace('gameasset', 'static/game'))

    def runDescription(self) -> str:
        elements = []
        isLowcast ='lowcast' in self.categoryDisplay().lower()
        if str(self.run.position) in self.run.settings['emotes']:
            elements.append(self.run.settings['emotes'][str(self.run.position)])
        elements.append(f'{FormatText.ordinalPosition(self.run.position)}')
        if self.run.oldPB['exists'] and int(self.run.oldPB['place']) > self.run.position:
            elements.append(f'(from {FormatText.ordinalPosition(int(self.run.oldPB['place']))})')
        elements.append('place:')
        elements.append(f'[{FormatText.convertTime(self.run.time, isLowcast=isLowcast)}]({self.run.weblink})')
        if self.run.oldPB['exists']:
            elements.append(f'({FormatText.convertTimeDifference(self.run.oldPB['time'], self.run.time, isLowcast=isLowcast)})')
        elements.append('by')
        elements.append(self.playersDisplay())
        if not self.run.oldPB['exists']:
            elements.append('\nFirst PB in the category!')
        
        return ' '.join(elements)
        
    def playersDisplay(self) -> str:
        playersInfo = self.run.playersInfo
        displayPlayers = ''
        for count, player in enumerate(playersInfo):
            if (len(playersInfo) > 1) and (count == len(playersInfo) - 1):
                displayPlayers += ' and '
            elif len(playersInfo) > 1 and count >= 1:
                displayPlayers += ', '
            if player['url'] is not None:
                flag = FormatText.getFlagEmoji(player["country"]) + " " if player["country"] != "" else ""
                displayPlayers += f'{flag}[{player["name"]}]({player["url"]})'
            else:
                displayPlayers += player['name']

        return displayPlayers
    
    def categoryDisplay(self) -> str:
        elements = []
        elements.append(self.run.gameInfo['name'])
        if not self.run.isFullGame:
            elements.append(self.run.levelInfo['name'])
        categoryElements = [self.run.categoryInfo['name']] + [value['name'] for value in self.run.valuesInfo]
        elements.append(', '.join(categoryElements))
        
        return '\n'.join(elements)
    

class StreamEmbed(discord.Embed):
    def __init__(self, streamData, gameData, userData) -> None:
        super().__init__()
        self.authorName = userData.name
        userAssets = [
            asset for asset in userData.staticAssets
            if asset.assetType == 'image'
        ]
        authorIcon = (
            'https://www.speedrun.com' + userAssets[0].path
            if userAssets else None
        )
        self.authorLink = 'https://www.speedrun.com/user/' + userData.url
        self.twitchLink = streamData.url
        title = streamData.title
        self.gameSettings = settings['games'].get(
            gameData.id,
            settings['games']['default']
        )
        gameName = self.gameSettings.get('display_name', gameData.name)
        self.colour = 0xffffff if streamData.hasPb else 0x000000
        self.title = title
        self.url = self.twitchLink
        self.area = streamData.areaId
        self.streamName = streamData.channelName
        self.set_image(
            url=streamData.previewUrl +
            '?vary=' + str(randint(0, 1_000_000))
        )
        self.label = f'{FormatText.getFlagEmoji(self.area)} **{self.streamName}** is streaming **{gameName}** {self.gameSettings["emote"]}'
        self.set_author(
            name=self.authorName,
            icon_url=authorIcon,
            url=self.authorLink
        )
        self.hasTherun = False
        self.therunWebsocket = None
        self.messages: list[discord.Message] = []

    async def setTherunProfileName(self):
        async with aiohttp.ClientSession() as therunSession:
            therunAPIlink = "https://therun.gg/api/users/" + self.streamName
            async with therunSession.get(therunAPIlink) as therunUserDataResponse:
                therunUserData = await therunUserDataResponse.json()
                if therunUserData != []:
                    self.hasTherun = True
                    self.therunLink = "https://therun.gg/"+self.streamName
                    print('connecting to websocket:', self.streamName)
                    self.therunListenTask = asyncio.create_task(self.listenToTherun())
                else:
                    self.therunLink =  ""

    def setButtonView(self):
        self.servers = self.gameSettings['stream_notif']
        self.view = ButtonView(self.authorLink, self.twitchLink, self.therunLink)

    async def sendMessages(self):
        for server in self.servers:
            message: discord.Message = await client.get_channel(server).send(self.label, embeds=[self], view=self.view)
            self.messages.append(message)

    async def listenToTherun(self):
        uri = "wss://fh76djw1t9.execute-api.eu-west-1.amazonaws.com/prod?username=" + self.streamName.lower()
        async with websockets.connect(uri) as self.therunWebsocket:
            while True:
                message = await self.therunWebsocket.recv()
                therunLiveUserData = json.loads(message)
                if therunLiveUserData != []:
                    therunUserData = therunLiveUserData["run"]
                    therunEmbed = TherunEmbed(therunUserData)
                    for message in self.messages:
                        await message.edit(content=self.label, embeds=[self, therunEmbed], view=self.view)

                

class TherunEmbed(discord.Embed):
    def __init__(self, therunUserData) -> None:
        super().__init__()
        self.data: dict = therunUserData
        self.user: str = therunUserData['user']
        self.game: str = therunUserData['game']
        self.category: str = therunUserData['category']
        self.currentSplitIndex: int = therunUserData['currentSplitIndex']
        self.currentSplitName: str = therunUserData['currentSplitName'] if therunUserData['currentSplitName'] else "-"
        self.totalSplitCount: int = len(therunUserData['splits'])
        self.runPercentage: float = float(therunUserData['runPercentage'])
        self.currentTime: float = therunUserData['currentTime']
        self.delta: float = therunUserData['delta']
        self.pb: float|None = therunUserData['pb']
        self.variables: dict[str, str] = therunUserData['variables']
        self.splits: list[dict] = therunUserData['splits']
        self.description: str = self.getDescription()
        self.colour: int = self.getColour()
        self.set_author(
            name = f"{self.game}\n{self.fullCategory()}",
            url = "https://therun.gg/live/" + self.user,
            icon_url = "https://therun.gg/media/logo/logo_dark_theme_no_text.png"
        )
        # self.set_thumbnail(url=self.getThumbnail())

    def deltaToTime(self) -> str:
        return FormatText.convertSplitTime(self.delta)
    
    def getColour(self) -> int:
        if self.delta < 0:
            return 0x00FF00
        elif self.delta > 0:
            return 0xFF0000
        else:
            return 0x808080
        
    def progressBar(self) -> str:
        return FormatText.numberToProgressBar(self.runPercentage, 15)
    
    def personalBest(self) -> str:
        if self.pb:
            return FormatText.convertTime(self.pb/1000)
        return "None"

    def fullCategory(self) -> str:
        return ', '.join([self.category, *self.variables.values()])
    
    def subsplitGroups(self) -> list[str]:
        subsplitGroups: list[str] = []
        splits = self.splits[:]
        splits.reverse()
        for split in splits:
            splitName: str = split['name']
            if splitName.startswith('{'):
                subsplitGroups.append(splitName.split('}')[0][1:])
            elif splitName.startswith('-') and len(subsplitGroups)>0:
                subsplitGroups.append(subsplitGroups[-1])
            else:
                subsplitGroups.append(splitName)
        subsplitGroups.reverse()

        return subsplitGroups

    def currentDisplaySplitName(self) -> str:
        if self.currentSplitName == '-':
            return '**-**'
        
        subsplitGroup = self.subsplitGroups()[self.currentSplitIndex]
        if self.currentSplitName.startswith('{'):
            currentSplitName = '}'.join(self.currentSplitName.split('}')[1:])
        elif self.currentSplitName.startswith('-'):
            currentSplitName = self.currentSplitName[1:]
        else:
            currentSplitName = self.currentSplitName

        if currentSplitName == subsplitGroup:
            return f'**{currentSplitName}** ({self.currentSplitIndex+1}/{self.totalSplitCount})'
        
        return f'***{subsplitGroup}*: {currentSplitName}** ({self.currentSplitIndex+1}/{self.totalSplitCount})'

    
    def getDescription(self) -> str:
        if self.currentSplitIndex < self.totalSplitCount:
            return \
                f"Personal Best: **{self.personalBest()}**\n\
                Current split: {self.currentDisplaySplitName()}\n\
                Current pace: **{self.deltaToTime()}**\n\
                Run progression: {self.progressBar()}".replace('                ', '')
        else:
            return \
                f"Personal Best: **{self.personalBest()}**\n\
                Final time: **{FormatText.convertTime(self.currentTime/1000)}**\n\
                Difference to PB: **{self.deltaToTime()}**\n\
                Run progression: {self.progressBar()}".replace('                ', '')
        

    def getThumbnail(self) -> str|None:
        if self.currentSplitIndex < 1:
            return None
        return get_graph(self.data)


class ButtonView(discord.ui.View):
    def __init__(self, authorLink, twitchLink, therunLink):
        super().__init__()
        self.authorLink = authorLink
        self.twitchLink = twitchLink
        self.therunLink = therunLink
        twitchButton = discord.ui.Button(emoji='<:twitch:1196142317615190066>', label='Twitch channel', url=self.twitchLink)
        self.add_item(twitchButton)
        srdcButton = discord.ui.Button(emoji='<:srdc:1196142314599485541>', label='Speedrun.com profile', url=self.authorLink)
        self.add_item(srdcButton)
        if self.therunLink != "":
            therunButton = discord.ui.Button(emoji='<:therun:1059863020601356388>', label='TheRun.gg profile', url=self.therunLink)
            self.add_item(therunButton)
    

class FormatText:
    @staticmethod
    def ordinalPosition(n: int) -> str:
        if 11 <= (n % 100) <= 13:
            suffix = 'th'
        else:
            suffix = ['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]

        return str(n) + suffix
    
    @staticmethod
    def getFlagEmoji(countryCode: str|None) -> str:
        if countryCode == None: return ''
        codePoints = [127397 + ord(char) for char in countryCode.upper()]

        return ''.join(chr(codePoint) for codePoint in codePoints)
    
    @staticmethod
    def convertTime(t: float, isLowcast: bool = False) -> str:
        hours = int(t/60/60)
        minutes = int(t/60%60)
        seconds = int(t%60)
        milliseconds = round(t%1*1000)

        if isLowcast:
            return "%d:%02d:%02d (%d casts)" % (minutes, seconds, milliseconds/10, hours)
        elif t > 600:
            return "%d:%02d:%02d" % (hours, minutes, seconds)
        else:
            return "%d:%02d.%03d" % (minutes, seconds, milliseconds) 
        
    @staticmethod
    def convertTimeDifference(t1: float, t2: float, isLowcast: bool = False) -> str:
        times = [t1, t2]
        times.sort()
        t1, t2 = times
        if isLowcast:
            casts = int(t2/3600) - int(t1/3600)
            t1 = int(t1%3600)*60 + (t1%1)*100
            t2 = int(t2%3600)*60 + (t2%1)*100
            sign = "-" if t2 > t1 else "+"

        t = abs(t2-t1)
        hours = int(t/3600)
        minutes = int(t/60%60)
        seconds = int(t%60)
        milliseconds = round(t%1*1000)

        if isLowcast:    
            if hours > 0:
                return "-%d casts, %s%d:%02d:%02d" % (casts, sign, hours, minutes, seconds)
            else:
                return "-%d casts, %s%d:%02d" % (casts, sign, minutes, seconds)

        elif t1 > 600:
            if hours > 0:
                return "-%d:%02d:%02d" % (hours, minutes, seconds)
            else:
                return "-%d:%02d" % (minutes, seconds)

        else:
            if hours > 0:
                return "-%d:%02d:%02d" % (hours, minutes, seconds)
            if minutes > 0:
                return "-%d:%02d.%03d" % (minutes, seconds, milliseconds) 
            else:
                return "-%d.%03d" % (seconds, milliseconds)


        
    @staticmethod
    def convertSplitTime(t: float) -> str:
        if t == 0:
            return "-0.00"
        
        sign = "-" if t < 0 else "+"
        t = abs(t/1000)
        hours = int(t/60/60)
        minutes = int(t/60%60)
        seconds = int(t%60)
        milliseconds = round(t%1*100)

        if t > 3600:
            return "%s%d:%02d:%02d.%02d" % (sign, hours, minutes, seconds, milliseconds)
        elif t > 60:
            return "%s%d:%02d.%02d" % (sign, minutes, seconds, milliseconds)
        else:
            return "%s%d.%02d" % (sign, seconds, milliseconds) 
        
    @staticmethod
    def numberToProgressBar(number: float, segments: int):
        fixed = round(number*segments*2)
        full = fixed//2
        half = fixed%2
        empty = segments - full - half

        return full*"█" + half*"▓" + empty*"░"




async def initialPrep() -> None:
    global rememberedRuns
    for series in settings['series']:
        endpoint = speedruncompy.GetLatestLeaderboard(seriesId=series, limit=100, vary=randint(0, 1_000_000))
        data = await endpoint.perform()
        rememberedRuns[series] = [run.id for run in data.runs]



client = commands.Bot(command_prefix='/', intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=True))
rememberedRuns: dict[str, list[str]] = {}
rememberedStreams: dict[str, StreamEmbed] = {}


@client.event
async def on_ready():
    print("Bot Online!")
    print("Name: {}".format(client.user.name))
    print("ID: {}".format(client.user.id))
    try: 
        synced = await client.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(e)
    await initialPrep()
    print("Ready!")
    tasks.loop(minutes = settings['loop_period'])(checkForNewRuns).start()
    tasks.loop(minutes = settings['loop_period'])(checkForNewStreams).start()



async def checkForNewRuns():
    global rememberedRuns

    newRuns: list[Run] = []
    for series in settings['series']:
        endpoint = speedruncompy.GetLatestLeaderboard(
            seriesId=series, 
            limit=50,
            vary=randint(0, 1_000_000)
            )
        data = await endpoint.perform()
        for run in data.runs:
            if run.id not in rememberedRuns[series]:
                endpoint = speedruncompy.GetRun(runId=run.id)
                data = await endpoint.perform()
                newRun = Run(
                    data.run,
                    data.category,
                    data.game,
                    data.level,
                    data.values,
                    data.variables,
                    data.users
                ) 

                if newRun.settings['il_mode'] == 0:
                    if not newRun.isFullGame:
                        continue

                await newRun.setPreviousPB()
                newRuns.append(newRun)
                rememberedRuns[series].append(newRun.id)

    for newRun in newRuns:
        print(datetime.now(UTC), 'new run:', newRun.weblink)
        sentMessage = await client.get_channel(newRun.settings['server']).send(newRun.pings, embed=RunEmbed(newRun))
        reactionEmote = newRun.settings['emotes'].get(str(newRun.position), None)
        if reactionEmote != None:
            await sentMessage.add_reaction(reactionEmote)

async def checkForNewStreams():
    seriesId = '15ndxp7r'
    endpoint = speedruncompy.GetStreamList(seriesId=seriesId, vary=randint(0, 1_000_000))
    data = await endpoint.perform()
    streamsToDelete = []
    for user in rememberedStreams.keys():
        if user not in [streamData.channelName for streamData in data.streamList]:
            streamsToDelete.append(user)
    for user in streamsToDelete:
        print(f"Deleting {user}'s stream")
        if rememberedStreams[user].hasTherun:
            rememberedStreams[user].therunListenTask.cancel()
        for m in rememberedStreams[user].messages: 
            await m.delete()
        del rememberedStreams[user]
    for streamEmbed in rememberedStreams.values():
        if streamEmbed.therunWebsocket:
            streamEmbed.therunListenTask.cancel()
            streamEmbed.therunListenTask = asyncio.create_task(streamEmbed.listenToTherun())
    for streamData in data.streamList:
        if streamData.channelName not in rememberedStreams:
            gameData = next(
                game for game in data.gameList
                if streamData.gameId == game.id
            )
            userData = next(
                user for user in data.userList
                if streamData.userId == user.id
            )
            streamEmbed = StreamEmbed(streamData, gameData, userData)
            await streamEmbed.setTherunProfileName()
            streamEmbed.setButtonView()
            await streamEmbed.sendMessages()
            rememberedStreams[streamData.channelName] = streamEmbed


@client.tree.command(name='run_to_embed')
@discord.app_commands.describe(run_id = 'ID of the run you want to embed')
async def run_to_embed(interaction: discord.Interaction, run_id: str):
    await interaction.response.defer()
    try:
        endpoint = speedruncompy.GetRun(runId=run_id)
        data = await endpoint.perform()
        runToEmbed = Run(
            data.run,
            data.category,
            data.game,
            data.level,
            data.values,
            data.variables,
            data.users
        ) 
        await runToEmbed.setPreviousPB()

        #await interaction.response.send_message(embeds=[RunEmbed(runToEmbed)])
        await interaction.followup.send(embeds=[RunEmbed(runToEmbed)])
    except speedruncompy.exceptions.BadRequest:
        #await interaction.response.send_message(content='Run not found.', ephemeral=True)
        await interaction.followup.send(content='Run not found.', ephemeral=True)


client.run(DISCORD_TOKEN)
