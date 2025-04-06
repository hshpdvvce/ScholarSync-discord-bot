import io
import os
import asyncio
import openai
import PyPDF2
import discord

# Set your OpenAI API key here or via environment variable
openai.api_key = os.getenv("OpenAI_API_KEY")

async def process_pdf(attachment: discord.Attachment, message: discord.Message):
    """Download the PDF, extract its text, and then prompt the user for AI processing."""
    try:
        pdf_bytes = await attachment.read()
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PyPDF2.PdfReader(pdf_file)
    except Exception as e:
        await message.channel.send("Error reading the PDF file.")
        return

    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text

    await prompt_ai_options(message, text)

class OptionSelect(discord.ui.Select):
    """Dropdown menu for selecting between summary and flashcards."""
    def __init__(self, user, pdf_text):
        self.user = user
        self.pdf_text = pdf_text
        options = [
            discord.SelectOption(label="Summary", value="summary", description="Generate a summary of the PDF"),
            discord.SelectOption(label="Flashcards", value="flashcards", description="Generate flashcards from the PDF")
        ]
        super().__init__(placeholder="Choose an option...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.user:
            await interaction.response.send_message("This selection is not for you.", ephemeral=True)
            return

        option = self.values[0]
        await interaction.response.defer()  # Acknowledge the interaction
        await process_ai(self.pdf_text, option, interaction.message)

class OptionView(discord.ui.View):
    """View containing the dropdown menu."""
    def __init__(self, user, pdf_text, timeout=60):
        super().__init__(timeout=timeout)
        self.add_item(OptionSelect(user, pdf_text))

async def prompt_ai_options(message: discord.Message, pdf_text: str):
    """Ask the user if they want a summary or flashcards generated from the PDF."""
    try:
        view = OptionView(message.author, pdf_text)
        await message.channel.send(
            "Please Select what the bot should do with the PDF:",
            view=view
        )
    except Exception as e:
        await message.channel.send("An error occurred while displaying the options.")

async def process_ai(text: str, option: str, message: discord.Message):
    """Use the OpenAI API to generate a summary or flashcards from the PDF text."""
    if option == "summary":
        prompt_text = f"Please summarize the following text:\n\n{text}"
    elif option == "flashcards":
        prompt_text = f"Generate flashcards (in Q&A format) based on the following text:\n\n{text}"
    else:
        await message.channel.send("Invalid option selected.")
        return

    try:
        response = openai.Completion.create(
            model="text-davinci-003",  # Modified: using model instead of engine
            prompt=prompt_text,
            max_tokens=150
        )
        result = response.choices[0].text.strip()
    except Exception as e:
        await message.channel.send("Error processing AI request.")
        return

    try:
        await message.author.send(f"Here is the {option} for your PDF:\n\n{result}")
    except Exception:
        await message.channel.send("Could not send you a DM. Please check your DM settings.")

async def process_message(message: discord.Message):
    """Checks if a message has a PDF attachment and processes it."""
    if not message.attachments:
        # No attachments in the message
        return

    for attachment in message.attachments:
        if attachment.filename.lower().endswith(".pdf"):
            await process_pdf(attachment, message)
            return

    # If no PDF attachments are found
    await message.channel.send("Please attach a PDF file for processing.")