print("Jai RadhaKrishna")

# To install the packages just type the following command in the terminal:
# pip install -r requirements.txt

import discord
from discord.ext import commands, tasks
from discord.ui import Select, View, Button
import datetime
import asyncio
import os
from keep_alive import keep_alive

bot = commands.Bot(command_prefix='-', help_command=None, intents=discord.Intents.all())

# Global storage for study groups.
# Each group dictionary includes:
# group_id, subject, max_members, created_by, created_at, expire_at,
# members (list of user ids), channel (text channel id), voice_channel (voice channel id),
# alert flags, and secret flag.
study_groups = {}
group_counter = 1  # To assign unique group IDs
user_groups = {}   # Mapping user_id -> group_id (to allow one group per user)

# ----------------- New Classes for Secret Group & Invite Handling -----------------
class SecretGroupSelect(Select):
    """Select menu to choose if the group should be secret."""
    def __init__(self, creator, secret_future: asyncio.Future):
        options = [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no")
        ]
        super().__init__(placeholder="Secret Group? (Yes/No)", min_values=1, max_values=1, options=options)
        self.creator = creator
        self.secret_future = secret_future

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.creator:
            await interaction.response.send_message("This selection is not for you.", ephemeral=True)
            return
        if not self.secret_future.done():
            self.secret_future.set_result(interaction.data["values"][0].lower())
        await interaction.response.send_message("Secret group selection recorded.", ephemeral=True)

class InviteSelect(Select):
    """Multi-select menu to choose server members to invite (or choose External Invite)."""
    def __init__(self, creator, guild):
        self.creator = creator
        self.selected_values = []
        options = []
        for member in guild.members:
            if not member.bot and member != creator:
                options.append(discord.SelectOption(label=member.display_name, value=str(member.id)))
        options.append(discord.SelectOption(label="External Invite", value="external"))
        super().__init__(placeholder="Select members to invite", min_values=0, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.creator:
            await interaction.response.send_message("This Selectin is **NOT** for You.", ephemeral=True)
            return
        self.selected_values = self.values
        await interaction.response.send_message("Please Click Confirm Again!😅", ephemeral=True)

class ConfirmInviteButton(Button):
    """Button to confirm the invite selection."""
    def __init__(self, creator):
        super().__init__(label="Confirm", style=discord.ButtonStyle.green)
        self.creator = creator
    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.creator:
            await interaction.response.send_message("This button is not for you.", ephemeral=True)
            return
        self.view.confirmed = True
        await interaction.response.send_message("Invite selection confirmed.", ephemeral=True)
        self.view.stop()

class InviteView(View):
    """View that contains the InviteSelect and a Confirm button."""
    def __init__(self, creator, guild, timeout=20):
        super().__init__(timeout=timeout)
        self.confirmed = False
        self.invite_select = InviteSelect(creator, guild)
        self.add_item(self.invite_select)
        self.add_item(ConfirmInviteButton(creator))
    async def on_timeout(self):
        self.stop()
# ------------------------------------------------------------------------------------

# Helper function to simulate ephemeral prompts for secret groups.
async def prompt_user_ephemeral(ctx, prompt: str) -> str:
    """Sends a prompt message, waits for a response from ctx.author, then deletes both the prompt and response."""
    prompt_msg = await ctx.send(prompt)
    try:
        response = await bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
        await prompt_msg.delete()
        await response.delete()
        return response.content
    except asyncio.TimeoutError:
        await prompt_msg.delete()
        await ctx.send("⏰ You took too long to respond. Please try again.", delete_after=5)
        return None

@bot.event
async def on_ready():
    print(f'Bot logged in as {bot.user.name}')
    check_expiry.start()

async def prompt_user(ctx, prompt: str) -> str:
    """Sends a prompt message and waits for a response from ctx.author."""
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
    """Returns the channel named 'general' or the first text channel if not found."""
    general = discord.utils.get(guild.text_channels, name="general")
    if not general:
        general = guild.text_channels[0]
    return general

@bot.command(name='create')
async def create_group(ctx):
    """Creates a study group interactively and sets up both text and voice channels."""
    global group_counter
    if ctx.author.id in user_groups:
        await ctx.send("⚠️ You are already in a study group. Use `-leave` to exit your current group before creating a new one.")
        return

    # ----- Ask Secret Group Question First -----
    secret_future = asyncio.get_running_loop().create_future()
    secret_view = View(timeout=20)
    secret_select = SecretGroupSelect(ctx.author, secret_future)
    secret_view.add_item(secret_select)
    secret_prompt = await ctx.send("🔒 Do you want to create a secret group? (Select Yes or No)", view=secret_view, delete_after=20)
    try:
        secret_choice = await asyncio.wait_for(secret_future, timeout=20.0)
    except asyncio.TimeoutError:
        secret_choice = "no"
    try:
        await secret_prompt.delete()
    except Exception:
        pass
    secret_flag = (secret_choice == "yes")
    # If secret group, delete the command invocation to avoid public trace.
    if secret_flag:
        try:
            await ctx.message.delete()
        except Exception:
            pass
    # ----------------------------------------------

    # Use ephemeral prompts if secret; else, use normal prompts.
    if secret_flag:
        subject = await prompt_user_ephemeral(ctx, "✏️ [Secret] Please enter the subject for the study group:")
        if subject is None:
            return
        duration_str = await prompt_user_ephemeral(ctx, "⏳ [Secret] For how many minutes should this group exist? (Enter a number)")
        if duration_str is None:
            return
        try:
            duration = int(duration_str)
            if duration < 1:
                await ctx.send("⚠️ Duration must be greater than 0.", delete_after=5)
                return
        except ValueError:
            await ctx.send("⚠️ Invalid duration. Try again.", delete_after=5)
            return
        max_members_str = await prompt_user_ephemeral(ctx, "👥 [Secret] How many people (including you) should be allowed in this group?")
        if max_members_str is None:
            return
        try:
            max_members = int(max_members_str)
            if max_members < 1:
                await ctx.send("⚠️ Enter a valid number greater than 0.", delete_after=5)
                return
        except ValueError:
            await ctx.send("⚠️ That doesn't look like a number. Try again.", delete_after=5)
            return
    else:
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

    current_group_id = group_counter
    study_groups[current_group_id] = {
        "group_id": current_group_id,
        "subject": subject,
        "max_members": max_members,
        "created_by": ctx.author.name,
        "created_at": created_at,
        "expire_at": expire_at,
        "members": [ctx.author.id],
        "channel": None,
        "voice_channel": None,
        "alerted_10": False,
        "alerted_5": False,
        "alerted_1": False,
        "secret": secret_flag
    }
    user_groups[ctx.author.id] = current_group_id

    guild = ctx.guild

    # Create a category for study groups if it doesn't exist.
    category = discord.utils.get(guild.categories, name="Study Groups")
    if not category:
        category = await guild.create_category("Study Groups")
    
    # Set up channel overwrites based on secret status.
    if secret_flag:
        text_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        voice_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            ctx.author: discord.PermissionOverwrite(view_channel=True, connect=True)
        }
    else:
        text_overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        voice_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
            ctx.author: discord.PermissionOverwrite(view_channel=True, connect=True)
        }

    text_channel_name = f"{subject}-{duration}min".replace(' ', '-').lower()
    group_text_channel = await guild.create_text_channel(text_channel_name, category=category, overwrites=text_overwrites)
    study_groups[current_group_id]["channel"] = group_text_channel.id

    voice_channel_name = f"{subject}-voice".replace(' ', '-').lower()
    group_voice_channel = await guild.create_voice_channel(voice_channel_name, category=category, overwrites=voice_overwrites)
    study_groups[current_group_id]["voice_channel"] = group_voice_channel.id

    # ----- Invite Prompt for Server Members (for both secret and public groups) -----
    try:
        await group_text_channel.send("📣 Use the **-invite** command to invite others to join your group!")
    except Exception:
        pass

    # For secret groups, show the invite view privately.
    if secret_flag:
        invite_view = InviteView(ctx.author, guild, timeout=20)
        invite_prompt = await ctx.send("👥 (Optional) [Secret] Select server members to invite (or choose 'External Invite') and click Confirm:", view=invite_view, ephemeral=True)
        await invite_view.wait()
        selected_invites = invite_view.invite_select.selected_values
        try:
            await invite_prompt.delete()
        except Exception:
            pass
        if selected_invites:
            for sel in selected_invites:
                if sel == "external":
                    invite = await group_text_channel.create_invite(max_age=0, unique=True)
                    try:
                        await ctx.author.send(f"External Invite Link for secret group '{subject}': {invite.url}")
                    except Exception:
                        pass
                else:
                    member_id = int(sel)
                    member = guild.get_member(member_id)
                    if member:
                        await group_text_channel.set_permissions(member, read_messages=True, send_messages=True)
                        await group_voice_channel.set_permissions(member, view_channel=True, connect=True)
                        if member_id not in study_groups[current_group_id]["members"]:
                            study_groups[current_group_id]["members"].append(member_id)
                            user_groups[member_id] = current_group_id
                        try:
                            await member.send(f"You have been invited to join the study group **'{group_text_channel.name}'** (ID {current_group_id}).\n**Text Channel:** {group_text_channel.mention}\n**Voice Channel:** {group_voice_channel.mention}")
                        except Exception:
                            pass
    # For public groups, no additional invite prompt here; users can use -share.
    # -----------------------------------------------------------------------------

    # For public groups, announce creation in general channel.
    if not secret_flag:
        general = get_general_channel(guild)
        expire_str = expire_at.strftime("%H:%M UTC")
        await general.send(f"✅ **Group Created:** ID **{current_group_id}** - **{subject}**. Expires at {expire_str}.")
    else:
        try:
            await ctx.author.send(f"✅ Secret study group created with ID **{current_group_id}**!\nText Channel: {group_text_channel.mention}\nVoice Channel: {group_voice_channel.mention}")
        except Exception:
            pass

    await ctx.send(f"✅ Study group created with ID **{current_group_id}**!\nText Channel: {group_text_channel.mention}\nVoice Channel: {group_voice_channel.mention}")
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
        secret_status = " (Secret)" if group.get("secret", False) else ""
        embed.add_field(
            name=f"Group ID {group['group_id']}: {group['subject']}{secret_status}",
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
            secret_status = " (Secret)" if group.get("secret", False) else ""
            label = f"Group {group['group_id']}: {group['subject']}{secret_status} ({len(group['members'])}/{group['max_members']})"
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

        text_channel = bot.get_channel(group["channel"])
        if text_channel:
            await text_channel.set_permissions(interaction.user, read_messages=True, send_messages=True)
        voice_channel = bot.get_channel(group["voice_channel"])
        if voice_channel:
            await voice_channel.set_permissions(interaction.user, view_channel=True, connect=True)

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
            secret_status = " (Secret)" if group.get("secret", False) else ""
            label = f"Group {group['group_id']}: {group['subject']}{secret_status}"
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

class ShareSelect(Select):
    """Dropdown to select a group to share."""
    def __init__(self):
        options = []
        for group in study_groups.values():
            secret_status = " (Secret)" if group.get("secret", False) else ""
            options.append(discord.SelectOption(label=f"Group {group['group_id']}: {group['subject']}{secret_status}",
                                                 description=f"Created by {group['created_by']}, {len(group['members'])}/{group['max_members']} members"))
        options.append(discord.SelectOption(label="None", description="Cancel and create your own group"))
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
                
class ShareView(View):
    def __init__(self):
        super().__init__()
        self.add_item(ShareSelect())

@bot.command(name='share')
async def share_groups(ctx):
    """Allows users to share study groups interactively by selecting a Group ID."""
    if not study_groups:
        await ctx.send("ℹ️ There are no study groups available to share.")
        return
    view = ShareView()
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
        text_channel = bot.get_channel(group["channel"])
        if text_channel:
            await text_channel.set_permissions(ctx.author, overwrite=None)
        voice_channel = bot.get_channel(group["voice_channel"])
        if voice_channel:
            await voice_channel.set_permissions(ctx.author, overwrite=None)
        await ctx.send(f"🚪 You have left Group {group_id}: {group['subject']}.")
        # Only public groups notify general.
        if not group.get("secret", False):
            guild = ctx.guild
            general = get_general_channel(guild)
            expire_str = group["expire_at"].strftime('%H:%M UTC')
            member_count = f"{len(group['members'])}/{group['max_members']}"
            await general.send(f"👤 **{ctx.author.name}** left Group **{group_id}: {group['subject']}**. Members: {member_count}. Expires at: {expire_str}.")
    else:
        await ctx.send("⚠️ Something went wrong. Could not leave the group.")

@bot.command(name='extend')
async def extend_group(ctx):
    """Allows a user to extend the expiration time of their study group (affecting both text and voice channels)."""
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
    guild = ctx.guild
    if not group.get("secret", False):
        general = get_general_channel(guild)
        await general.send(f"⏳ **Group Extended:** Group {group_id} - {group['subject']} now expires at {new_expire_str}.")

# New command: -invite (for users already in a group)
@bot.command(name='invite')
async def invite_command(ctx):
    """Allows a user already in a group to invite more members."""
    if ctx.author.id not in user_groups:
        await ctx.send("⚠️ You are not in any study group.")
        return
    group_id = user_groups[ctx.author.id]
    group = study_groups.get(group_id)
    if group is None:
        await ctx.send("⚠️ Group not found.")
        return
    if len(group["members"]) >= group["max_members"]:
        await ctx.send("⚠️ Your group is already full.")
        return
    # Inform in the group text channel to use -invite.
    group_text_channel = bot.get_channel(group["channel"])
    if group_text_channel:
        await group_text_channel.send("📣 Use the **-invite** command to invite others to join your group!")
    # Show invite selection view.
    invite_view = InviteView(ctx.author, ctx.guild, timeout=20)
    invite_prompt = await ctx.send("👥 Select server members to invite (or choose 'External Invite') and click Confirm:", view=invite_view, ephemeral=True)
    await invite_view.wait()
    selected_invites = invite_view.invite_select.selected_values
    try:
        await invite_prompt.delete()
    except Exception:
        pass
    if selected_invites:
        for sel in selected_invites:
            if sel == "external":
                invite = await group_text_channel.create_invite(max_age=0, unique=True)
                try:
                    await ctx.author.send(f"External Invite Link for group '{group['subject']}': {invite.url}")
                except Exception:
                    pass
            else:
                member_id = int(sel)
                member = ctx.guild.get_member(member_id)
                if member:
                    await group_text_channel.set_permissions(member, read_messages=True, send_messages=True)
                    voice_channel = bot.get_channel(group["voice_channel"])
                    if voice_channel:
                        await voice_channel.set_permissions(member, view_channel=True, connect=True)
                    if member_id not in group["members"]:
                        group["members"].append(member_id)
                        user_groups[member_id] = group_id
                    try:
                        await member.send(
                            f"You have been invited to join the study group **'{group['subject']}'** (ID {group_id}).\n"
                            f"CHECK OUT \nText Channel {group_text_channel.mention} \nVoice Channel {voice_channel.mention}"
                        )
                    except Exception:
                        pass
    await ctx.send("✅ Invite processing complete.", delete_after=5)

@bot.command(name='secret')
@commands.has_permissions(administrator=True)
async def secret_groups(ctx):
    """(Admin Only) Displays details of all secret groups."""
    secret_info = []
    for group in study_groups.values():
        if group.get("secret", False):
            expire_str = group["expire_at"].strftime("%Y-%m-%d %H:%M UTC")
            channel = bot.get_channel(group["channel"])
            channel_name = channel.name if channel else "N/A"
            secret_info.append(f"ID {group['group_id']}: {group['subject']} | Channel: {channel_name} | Created by: {group['created_by']} | Expires at: {expire_str}")
    if secret_info:
        response = "\n".join(secret_info)
    else:
        response = "No secret groups found."
    await ctx.send(response)

@bot.command(name='help')
async def help_command(ctx):
    """
    Displays the list of available commands.
    Usage: -help
    """
    embed = discord.Embed(
        title="📚 **ScholarSync Bot Commands**",
        description="Use the commands below to manage your study groups and boost your study sessions!",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(name="**-create**", value="🤓 Create a new study group. You'll be asked for **subject, duration (minutes), and max members**. You will also be asked if you want to make it secret and to invite members.", inline=False)
    embed.add_field(name="**-join**", value="👥 Join an existing study group via dropdown. (One group per user)", inline=False)
    embed.add_field(name="**-leave**", value="🚪 Leave your current study group.", inline=False)
    embed.add_field(name="**-list**", value="📋 View all active study groups with details.", inline=False)
    embed.add_field(name="**-share**", value="📢 Share study group details with others.", inline=False)
    embed.add_field(name="**-members**", value="👤 View all members in a study group.", inline=False)
    embed.add_field(name="**-extend**", value="⏳ Extend the expiration time of your study group.", inline=False)
    embed.add_field(name="**-invite**", value="✉️ (In-group) Invite additional members to your group.", inline=False)
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
        total_duration = (group["expire_at"] - group["created_at"]).total_seconds() if group.get("created_at") else 0
        channel = bot.get_channel(group["channel"]) if group.get("channel") else None

        if total_duration >= 600 and time_left <= 600 and time_left > 300 and not group.get("alerted_10", False):
            if channel:
                await channel.send("⏰ **Alert:** This group will end in **10 minutes**! Type **-extend** to extend the time.")
            group["alerted_10"] = True

        if total_duration >= 300 and time_left <= 300 and time_left > 60 and not group.get("alerted_5", False):
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
            if not group.get("secret", False):
                general = get_general_channel(guild)
            else:
                general = None
            channel = bot.get_channel(group["channel"]) if group.get("channel") else None
            if channel:
                await channel.send("🗑️ This study group has now ended.")
                await channel.delete()
            voice_channel = bot.get_channel(group["voice_channel"]) if group.get("voice_channel") else None
            if voice_channel:
                await voice_channel.delete()
            if general:
                await general.send(f"🗑️ **Group Deleted:** ID **{group_id}** - **{group['subject']}** has been deleted as per the set time.")
            for user_id in group["members"]:
                if user_groups.get(user_id) == group_id:
                    user_groups.pop(user_id, None)

keep_alive()

try:
    bot.run(os.getenv("TOKEN"))
except discord.errors.HTTPException as e:
    print(f"Failed to connect to Discord: {e}")
    print("Please check your bot token and internet connection")