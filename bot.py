print("Jai RadhaKrishna")

import discord
from discord.ext import commands, tasks
from discord.ui import Select, View
import datetime
import asyncio
import os

# Set up the bot with "-" as the command prefix and disable the default help command.
bot = commands.Bot(command_prefix='-', help_command=None, intents=discord.Intents.all())

# Global storage for study groups.
# Each group dictionary includes:
# group_id, subject, max_members, created_by, created_at, expire_at,
# members (list of user ids), channel (temporary channel id), and alert flags.
study_groups = {}
group_counter = 1  # To assign unique group IDs
user_groups = {}   # Mapping user_id -> group_id (to allow one group per user)

@bot.event
async def on_ready():
    print(f'Bot logged in as {bot.user.name}')
    check_expiry.start()  # Start background task to monitor group expiry

async def prompt_user(ctx, prompt: str) -> str:
    """Send a prompt message and wait for the user's response."""
    await ctx.send(prompt)
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel
    try:
        msg = await bot.wait_for("message", timeout=60.0, check=check)
        return msg.content
    except asyncio.TimeoutError:
        await ctx.send("⏰ You took too long to respond. Please try again.")
        return None

def get_general_channel(guild: discord.Guild) -> discord.TextChannel:
    """Attempt to find a channel named 'general' in the guild; if not found, return the first text channel."""
    general = discord.utils.get(guild.text_channels, name="general")
    if not general:
        general = guild.text_channels[0]
    return general

@bot.command(name='create')
async def create_group(ctx):
    """Creates a study group interactively using a duration from now."""
    global group_counter
    if ctx.author.id in user_groups:
        await ctx.send("⚠️ You are already in a study group. Use `-leave` to exit your current group before creating a new one.")
        return

    subject = await prompt_user(ctx, "✏️ Please enter the **subject** for the study group:")
    if subject is None:
        return

    duration_str = await prompt_user(ctx, "⏳ For how many minutes should this group exist? (Enter a number)")
    if duration_str is None:
        return
    try:
        duration = int(duration_str)
        if duration < 1:
            await ctx.send("⚠️ Duration must be greater than 0.")
            return
    except ValueError:
        await ctx.send("⚠️ Invalid duration. Try again.")
        return

    max_members_str = await prompt_user(ctx, "👥 How many people (including you) should be allowed in this group?")
    if max_members_str is None:
        return
    try:
        max_members = int(max_members_str)
        if max_members < 1:
            await ctx.send("⚠️ Enter a valid number greater than 0.")
            return
    except ValueError:
        await ctx.send("⚠️ That doesn't look like a number. Try again.")
        return

    now = datetime.datetime.utcnow()
    created_at = now
    expire_at = now + datetime.timedelta(minutes=duration)

    study_groups[group_counter] = {
        "group_id": group_counter,
        "subject": subject,
        "max_members": max_members,
        "created_by": ctx.author.name,
        "created_at": created_at,
        "expire_at": expire_at,
        "members": [ctx.author.id],
        "channel": None,
        "alerted_10": False,
        "alerted_5": False,
        "alerted_1": False
    }
    user_groups[ctx.author.id] = group_counter

    # Create a temporary channel with permission overwrites:
    # Everyone can view messages; only group members can send messages.
    guild = ctx.guild
    category = discord.utils.get(guild.categories, name="Study Groups")
    if not category:
        category = await guild.create_category("Study Groups")
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    channel_name = f"{subject}-{duration}min".replace(' ', '-').lower()
    group_channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
    study_groups[group_counter]["channel"] = group_channel.id

    general = get_general_channel(guild)
    expire_str = expire_at.strftime("%H:%M UTC")
    await general.send(f"✅ **Group Created:** ID **{group_counter}** - **{subject}**. Expires at {expire_str}.")
    await ctx.send(f"✅ Study group created with ID **{group_counter}**! Temporary room: {group_channel.mention}")
    group_counter += 1

@bot.command(name='list')
async def list_groups(ctx):
    """Lists all available study groups."""
    if not study_groups:
        await ctx.send("ℹ️ There are no study groups created yet.")
        return

    embed = discord.Embed(title="Study Groups Overview", color=discord.Color.blue(), timestamp=datetime.datetime.utcnow())
    for group in study_groups.values():
        created_time = group["created_at"].strftime("%Y-%m-%d %H:%M UTC")
        member_count = f"{len(group['members'])}/{group['max_members']}"
        expire_str = group["expire_at"].strftime("%H:%M UTC")
        embed.add_field(
            name=f"Group ID {group['group_id']}: {group['subject']}",
            value=(f"**Created by:** {group['created_by']}\n"
                   f"**Created at:** {created_time}\n"
                   f"**Expires at:** {expire_str}\n"
                   f"**Members:** {member_count}"),
            inline=False
        )
    await ctx.send(embed=embed)

class GroupSelect(Select):
    """Dropdown menu for joining study groups."""
    def __init__(self):
        options = []
        for group in study_groups.values():
            label = f"Group {group['group_id']}: {group['subject']} ({len(group['members'])}/{group['max_members']})"
            options.append(discord.SelectOption(label=label, value=str(group['group_id'])))
        options.append(discord.SelectOption(label="None (Create your own group)", value="none"))
        super().__init__(placeholder="Select a study group...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        if selected_value == "none":
            await interaction.response.send_message("ℹ️ Create your own group using **-create**.", ephemeral=True)
            return
        try:
            group_id = int(selected_value)
        except ValueError:
            await interaction.response.send_message("⚠️ Something went wrong. Try again.", ephemeral=True)
            return
        if group_id not in study_groups:
            await interaction.response.send_message("⚠️ The selected group no longer exists.", ephemeral=True)
            return
        group = study_groups[group_id]
        if interaction.user.id in group["members"]:
            await interaction.response.send_message("ℹ️ You're already in this group.", ephemeral=True)
            return
        if len(group["members"]) >= group["max_members"]:
            await interaction.response.send_message("⚠️ Sorry, this group is full.", ephemeral=True)
            return
        if interaction.user.id in user_groups:
            await interaction.response.send_message("⚠️ You are already in a study group. Use `-leave` to exit your current group.", ephemeral=True)
            return

        group["members"].append(interaction.user.id)
        user_groups[interaction.user.id] = group_id

        # Update channel permissions for the new member.
        channel = bot.get_channel(group["channel"])
        if channel:
            await channel.set_permissions(interaction.user, read_messages=True, send_messages=True)

        await interaction.response.send_message(f"✅ You joined Group {group_id}: {group['subject']}.", ephemeral=True)
        guild = interaction.guild
        general = get_general_channel(guild)
        expire_str = group["expire_at"].strftime("%H:%M UTC")
        member_count = f"{len(group['members'])}/{group['max_members']}"
        await general.send(f"👤 **{interaction.user.name}** joined Group **{group_id}: {group['subject']}**. Members: {member_count}. Expires at: {expire_str}.")

class GroupJoinView(View):
    def __init__(self):
        super().__init__()
        self.add_item(GroupSelect())

@bot.command(name='join')
async def join_group(ctx):
    """Allows users to join an existing study group via dropdown."""
    if ctx.author.id in user_groups:
        await ctx.send("⚠️ You are already in a study group. Use **-leave** to exit your current group before joining another.")
        return
    if not study_groups:
        await ctx.send("ℹ️ There are no existing study groups. Use **-create** to start one.")
        return

    view = GroupJoinView()
    await ctx.send("Select a study group from the dropdown:", view=view)

class MembersSelect(Select):
    """Dropdown to select a group to view its members."""
    def __init__(self):
        options = []
        for group in study_groups.values():
            label = f"Group {group['group_id']}: {group['subject']}"
            options.append(discord.SelectOption(label=label, value=str(group['group_id'])))
        super().__init__(placeholder="Select a group to view its members...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        try:
            group_id = int(self.values[0])
        except ValueError:
            await interaction.response.send_message("⚠️ Invalid selection.", ephemeral=True)
            return
        if group_id not in study_groups:
            await interaction.response.send_message("⚠️ The selected group no longer exists.", ephemeral=True)
            return
        group = study_groups[group_id]
        member_names = []
        for user_id in group["members"]:
            member = interaction.guild.get_member(user_id)
            if member:
                member_names.append(member.display_name)
        members_str = ", ".join(member_names) if member_names else "No members found."
        await interaction.response.send_message(f"**Members in Group {group_id} ({group['subject']}):**\n{members_str}", ephemeral=True)

class MembersView(View):
    def __init__(self):
        super().__init__()
        self.add_item(MembersSelect())

@bot.command(name='members')
async def show_members(ctx):
    """Displays members of a selected study group via dropdown."""
    if not study_groups:
        await ctx.send("ℹ️ There are no study groups created yet.")
        return
    view = MembersView()
    await ctx.send("Select a study group to view its members:", view=view)

@bot.command(name='share')
async def share_groups(ctx):
    """Allows users to share study groups interactively by selecting a Group ID."""
    if not study_groups:
        await ctx.send("ℹ️ There are no study groups available to share.")
        return

    options = [
        discord.SelectOption(label=f"Group {group['group_id']}: {group['subject']}", 
                             description=f"Created by {group['created_by']}, {len(group['members'])}/{group['max_members']} members")
        for group in study_groups.values()
    ]
    options.append(discord.SelectOption(label="None", description="Cancel and create your own group"))

    class ShareSelect(Select):
        def __init__(self):
            super().__init__(placeholder="📚 Select a study group to share", options=options, min_values=1, max_values=1)

        async def callback(self, interaction: discord.Interaction):
            selected_value = self.values[0]
            if selected_value == "None":
                await interaction.response.send_message("❌ You chose not to share any group. Use `-create` to start your own!", ephemeral=True)
            else:
                group_id = int(selected_value.split(':')[0].split()[-1])
                group = study_groups.get(group_id)
                if group:
                    member_count = f"{len(group['members'])}/{group['max_members']}"
                    embed = discord.Embed(
                        title=f"📢 Study Group {group_id}: {group['subject']}",
                        color=discord.Color.green(),
                        timestamp=datetime.datetime.utcnow()
                    )
                    embed.add_field(name="👤 Created By", value=group["created_by"], inline=True)
                    embed.add_field(name="👥 Members", value=member_count, inline=True)
                    embed.add_field(name="⏳ Expires at", value=group["expire_at"].strftime("%H:%M UTC"), inline=True)
                    embed.set_footer(text="Share this message to invite more members!")
                    await interaction.response.send_message(embed=embed)
    view = View()
    view.add_item(ShareSelect())
    await ctx.send("📢 **Select a study group to share:**", view=view)

@bot.command(name='leave')
async def leave_group(ctx):
    """Allows a user to leave the study group they have joined."""
    if ctx.author.id not in user_groups:
        await ctx.send("⚠️ You are not in any study group.")
        return

    group_id = user_groups.pop(ctx.author.id)
    group = study_groups.get(group_id)
    if group and ctx.author.id in group["members"]:
        group["members"].remove(ctx.author.id)
        channel = bot.get_channel(group["channel"])
        if channel:
            await channel.set_permissions(ctx.author, overwrite=None)
        await ctx.send(f"🚪 You have left Group {group_id}: {group['subject']}.")
        guild = ctx.guild
        general = get_general_channel(guild)
        expire_str = group["expire_at"].strftime('%H:%M UTC')
        member_count = f"{len(group['members'])}/{group['max_members']}"
        await general.send(f"👤 **{ctx.author.name}** left Group **{group_id}: {group['subject']}**. Members: {member_count}. Expires at: {expire_str}.")
    else:
        await ctx.send("⚠️ Something went wrong. Could not leave the group.")

@bot.command(name='extend')
async def extend_group(ctx):
    """Allows a user to extend the expiration time of their study group."""
    if ctx.author.id not in user_groups:
        await ctx.send("⚠️ You are not in any study group.")
        return
    group_id = user_groups[ctx.author.id]
    group = study_groups.get(group_id)
    if not group:
        await ctx.send("⚠️ Group not found.")
        return

    extension_str = await prompt_user(ctx, "⏳ How many minutes do you want to extend the group?")
    if extension_str is None:
        return
    try:
        extension = int(extension_str)
        if extension < 1:
            await ctx.send("⚠️ Extension must be at least 1 minute.")
            return
    except ValueError:
        await ctx.send("⚠️ Invalid number.")
        return

    group['expire_at'] += datetime.timedelta(minutes=extension)
    # Reset alert flags so that alerts are triggered again after extension.
    group["alerted_10"] = False
    group["alerted_5"] = False
    group["alerted_1"] = False

    new_expire_str = group['expire_at'].strftime('%H:%M UTC')
    await ctx.send(f"✅ Group {group_id} extended. New expiration time: {new_expire_str}.")
    general = get_general_channel(ctx.guild)
    await general.send(f"⏳ **Group Extended:** Group {group_id} - {group['subject']} now expires at {new_expire_str}.")

@bot.command(name='help')
async def help_command(ctx):
    """
    Displays the list of available commands.
    Usage: -help
    """
    embed = discord.Embed(
        title="📚 **ScholarSync Bot Commands**",
        description="Use the commands below to manage your study groups efficiently!",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(name="**-create**", value="🤓 Create a new study group. You'll be asked for **subject, duration (minutes), and max members**.", inline=False)
    embed.add_field(name="**-join**", value="👥 Join an existing study group via dropdown. (One group per user)", inline=False)
    embed.add_field(name="**-leave**", value="🚪 Leave your current study group.", inline=False)
    embed.add_field(name="**-list**", value="📋 View all active study groups with details.", inline=False)
    embed.add_field(name="**-share**", value="📢 Share study group details with others.", inline=False)
    embed.add_field(name="**-members**", value="👤 View all members in a study group.", inline=False)
    embed.add_field(name="**-extend**", value="⏳ Extend the expiration time of your study group.", inline=False)
    embed.set_footer(text="Happy Studying! 🚀")
    await ctx.send(embed=embed)

@bot.command(name='clearall')
@commands.has_permissions(administrator=True)
async def clear_all(ctx):
    """
    Clears all messages in the current channel.
    (This command is for administrative use only and is not shown in the help command.)
    """
    deleted = await ctx.channel.purge(limit=None)
    await ctx.send(f"🗑️ Cleared {len(deleted)} messages in this channel.", delete_after=3)

# Background task to check for group expiration and send alerts.
@tasks.loop(minutes=1)
async def check_expiry():
    now = datetime.datetime.utcnow()
    expired_groups = []
    for group_id, group in list(study_groups.items()):
        time_left = (group["expire_at"] - now).total_seconds()
        channel = bot.get_channel(group["channel"]) if group.get("channel") else None

        # Send alerts at 10, 5, and 1 minute(s) remaining.
        if time_left <= 600 and time_left > 300 and not group.get("alerted_10", False):
            if channel:
                await channel.send("⏰ **Alert:** This group will end in **10 minutes**! Type **-extend** to extend the time.")
            group["alerted_10"] = True
        if time_left <= 300 and time_left > 60 and not group.get("alerted_5", False):
            if channel:
                await channel.send("⏰ **Alert:** This group will end in **5 minutes**! Type **-extend** to extend the time.")
            group["alerted_5"] = True
        if time_left <= 60 and time_left > 0 and not group.get("alerted_1", False):
            if channel:
                await channel.send("⏰ **Alert:** This group will end in **1 minute**! Type **-extend** to extend the time.")
            group["alerted_1"] = True

        if time_left <= 0:
            expired_groups.append(group_id)

    for group_id in expired_groups:
        group = study_groups.pop(group_id, None)
        if group:
            guild = bot.guilds[0]
            general = get_general_channel(guild)
            channel = bot.get_channel(group["channel"]) if group.get("channel") else None
            if channel:
                await channel.send("🗑️ This study group has now ended.")
                await channel.delete()
            await general.send(f"🗑️ **Group Deleted:** ID **{group_id}** - **{group['subject']}** has been deleted as per the set time.")
            for user_id in group["members"]:
                if user_groups.get(user_id) == group_id:
                    user_groups.pop(user_id, None)

bot.run(os.getenv("TOKEN"))