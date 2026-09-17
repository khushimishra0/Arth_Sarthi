"""Every user-facing string that is not produced by a feature engine.

Two reasons this is one module rather than f-strings scattered through handlers.
Phase 7 adds Hindi: with the copy here that is a data edit, not a rewrite of the
bot. And the web dashboard shows the same welcome as Telegram — same words, same
promises — because both call the same function.

The `/start` text is the most carefully weighted thing in the file. §8: "Trust is
a feature, not a footer." The people this product is for have been sold policies
they did not understand by agents who did not explain them, and have been asked
for an OTP by an app that then emptied their account. So the first three lines
state what ArthaSathi will never ask for and that it sells nothing — before it
says a single word about what it can do.
"""

from __future__ import annotations

from app.channels.base import Block, Button, Keyboard, OutboundMessage
from app.models.enums import Language

__all__ = [
    "DISCLAIMER",
    "MAIN_ACTIONS",
    "fallback",
    "help_message",
    "main_keyboard",
    "trust_lines",
    "welcome",
]

DISCLAIMER = "Educational guidance, not financial or legal advice."

# (callback token, English label). Order is the order on screen.
MAIN_ACTIONS: tuple[tuple[str, str], ...] = (
    ("scam_check", "🚩 Check a message"),
    ("loan_analysis", "📄 Analyse a loan"),
    ("schemes", "🏛️ Govt schemes"),
    ("budget", "💰 Plan my month"),
)

_STRINGS: dict[Language, dict[str, str]] = {
    Language.ENGLISH: {
        "greeting": "🙏 Namaste. I am ArthaSathi.",
        "trust_1": (
            "I will never ask for your bank password, PIN, OTP, CVV or card number. "
            "Nobody honest ever will."
        ),
        "trust_2": (
            "I sell nothing — no loan, no insurance, no investment. "
            "Nobody pays me to send you anywhere."
        ),
        "trust_3": (
            "Screenshots and documents you send are read, then deleted immediately. "
            "They are never stored and never shared."
        ),
        "what_i_do": "Four things I can help with:",
        "do_scam": "🚩 Check if a message or call is a scam — send me a screenshot",
        "do_loan": "📄 Read a loan paper and show the real cost — send me the PDF",
        "do_schemes": "🏛️ Find government schemes you qualify for — seven quick taps",
        "do_budget": "💰 Plan your month and set a savings goal — just type your numbers",
        "pick_one": "Tap a button below, or just send me whatever you are worried about.",
        "help_heading": "What I can do",
        "help_commands": "Commands, if you prefer typing:",
        "help_no_command": (
            "You do not need any of them. Send a photo and I check it for scams. "
            "Send a PDF and I read the loan. Forward me anything suspicious."
        ),
        "not_a_trading_app": (
            "⚠️ I am not a trading or investment app. I will never tell you to buy a "
            "particular stock, fund or policy. I explain, warn and calculate — the "
            "decision stays yours."
        ),
        "privacy_line": (
            "Your documents are never stored. Send /profile to see or delete "
            "everything I keep."
        ),
        "fallback": "I did not quite catch that — but nothing is broken. Here is what I can do:",
        "coming_soon": "That is being built right now — it lands in the next phase.",
        "send_screenshot": (
            "Send me the screenshot of the message, or forward the message itself. 📸"
        ),
        "send_pdf": (
            "Send me the loan document as a PDF and I will work out what it really costs. 📄"
        ),
        "schemes_intro": (
            "I will ask you seven quick questions, all buttons — no typing. "
            "You can tap ← Back any time, and skip any question you would rather not answer."
        ),
        "budget_intro": (
            "Tell me your monthly income and what you spend on. You can type it all "
            "in one line, like:\nincome 25000 rent 8000 food 4000 travel 3000"
        ),
        # --- Web dashboard (Phase 7) ------------------------------------------
        # The bot's words are conversational ("send me a screenshot"); the web
        # needs the same promise in the register of a page heading. Same product,
        # same guarantees — a different surface, so a few strings differ.
        "brand": "ArthaSathi",
        "tagline": "India's financial safety layer — the same help that lives on Telegram.",
        "nav_scam": "Scam check",
        "nav_loan": "Loan analysis",
        "nav_schemes": "Govt schemes",
        "nav_budget": "Budget",
        "nav_demo": "Demo",
        "footer": (
            "Educational guidance, not financial or legal advice. Nothing you upload is stored."
        ),
        "home_title": "What are you worried about?",
        "home_lede": (
            "Four tools, no account, nothing stored. The same engines answer here and "
            "on Telegram — every number is computed, every warning names its reason."
        ),
        "card_scam_title": "Is this a scam?",
        "card_scam_desc": "Paste a suspicious message or upload a screenshot.",
        "card_loan_title": "What does this loan really cost?",
        "card_loan_desc": "Upload the loan PDF and see the true rate and hidden fees.",
        "card_schemes_title": "Which govt schemes fit me?",
        "card_schemes_desc": "Answer seven questions. Only schemes you qualify for.",
        "card_budget_title": "Plan my month",
        "card_budget_desc": "Type your income and expenses; see where it goes.",
        # Scam page
        "scam_page_title": "Check a message for a scam",
        "scam_page_lede": (
            "Paste the message text, or upload a screenshot. It is read, scored against "
            "named rules, then deleted — never stored, never shared."
        ),
        "scam_text_label": "Paste the message",
        "scam_text_ph": "e.g. Congratulations! Your number won ₹25,00,000. Pay ₹4,999 to claim.",
        "scam_image_label": "…or upload a screenshot",
        "scam_submit": "Check it",
        "scam_need_input": (
            "Paste some text or choose a screenshot first — I need something to read."
        ),
        # Loan page
        "loan_page_title": "See what a loan really costs",
        "loan_page_lede": (
            "Upload the sanction letter or loan agreement as a PDF. Every rupee shown is "
            "computed in code from the figures on the page — nothing is invented."
        ),
        "loan_file_label": "Loan document (PDF)",
        "loan_submit": "Analyse it",
        "loan_need_input": "Choose a PDF first.",
        # Schemes page
        "schemes_page_title": "Find government schemes you qualify for",
        "schemes_page_lede": (
            "Seven questions, all optional — leaving one blank only widens the results. "
            "Eligibility is decided in code before anything is shown."
        ),
        "q_gender": "Gender",
        "q_age": "Age",
        "q_state": "State or union territory",
        "q_area": "Where you live",
        "q_income": "Monthly household income",
        "q_caste": "Category",
        "q_needs": "What do you need help with?",
        "opt_blank": "Prefer not to say",
        "schemes_submit": "Find schemes",
        "schemes_need_need": "Pick at least one thing you need help with.",
        # Budget page
        "budget_page_title": "Plan your month",
        "budget_page_lede": (
            "Type your income and what you spend on. The chart updates as you type; the "
            "advice and savings goal are computed when you submit."
        ),
        "budget_text_label": "Your month, in one line",
        "budget_text_ph": "income 25000 rent 8000 food 4000 travel 3000 phone 500",
        "budget_preview": "Live preview",
        "budget_submit": "Plan it",
        "budget_need_input": (
            "Type your income and at least one expense, like: income 25000 rent 8000."
        ),
        # Result page + shared
        "result_back": "← Start over",
        "copy_text": "Copy as text",
        "upload_too_big": "That file is larger than {mb} MB. Please upload a smaller one.",
        "bad_upload": "I could not read that upload. Please try a different file.",
        "demo_page_title": "Four worked examples",
        "demo_page_lede": "One click each — the same four cases from the walkthrough.",
    },
}

# Hindi. Devanagari, not transliterated Hinglish — somebody who reads Hindi reads
# Devanagari, and Hinglish signals "translated for you" rather than "written for you".
#
# The register is deliberately plain: "ब्याज" not "व्याज दर प्रतिशत", "पैसा" where
# "राशि" would be stiff. These are people who were talked past by an agent using
# exactly the vocabulary a bank uses, so this speaks the way a careful relative
# would. Any key missing here still falls through to English rather than breaking.
_STRINGS[Language.HINDI] = {
    "greeting": "🙏 नमस्ते। मैं अर्थसाथी हूँ।",
    "trust_1": (
        "मैं आपसे आपका बैंक पासवर्ड, पिन, ओटीपी, सीवीवी या कार्ड नंबर कभी नहीं माँगूँगा। "
        "कोई भी ईमानदार व्यक्ति या संस्था कभी नहीं माँगती।"
    ),
    "trust_2": (
        "मैं कुछ भी नहीं बेचता — न कोई लोन, न बीमा, न कोई निवेश। "
        "मुझे कोई पैसा नहीं देता कि मैं आपको कहीं भेजूँ।"
    ),
    "trust_3": (
        "आप जो स्क्रीनशॉट या कागज़ भेजते हैं, वे पढ़े जाते हैं और तुरंत मिटा दिए जाते हैं। "
        "उन्हें कभी सहेजा नहीं जाता और किसी को नहीं दिया जाता।"
    ),
    "what_i_do": "मैं चार कामों में मदद कर सकता हूँ:",
    "do_scam": "🚩 पता करें कि कोई मैसेज या कॉल धोखा है या नहीं — मुझे स्क्रीनशॉट भेजें",
    "do_loan": "📄 लोन का कागज़ पढ़कर उसकी असली लागत बताऊँ — पीडीएफ भेजें",
    "do_schemes": "🏛️ जानें कि आप किन सरकारी योजनाओं के पात्र हैं — सात आसान टैप",
    "do_budget": "💰 महीने का हिसाब बनाएँ और बचत का लक्ष्य रखें — बस अपने आंकड़े लिखें",
    "pick_one": "नीचे कोई बटन दबाएँ, या जो भी चिंता हो सीधे मुझे भेज दें।",
    "help_heading": "मैं क्या कर सकता हूँ",
    "help_commands": "अगर टाइप करना पसंद हो, तो ये कमांड हैं:",
    "help_no_command": (
        "इनकी ज़रूरत नहीं है। फोटो भेजें, मैं धोखे की जाँच कर दूँगा। "
        "पीडीएफ भेजें, मैं लोन पढ़ लूँगा। कुछ भी संदिग्ध हो तो मुझे फॉरवर्ड कर दें।"
    ),
    "not_a_trading_app": (
        "⚠️ मैं कोई ट्रेडिंग या निवेश ऐप नहीं हूँ। मैं आपको कभी नहीं कहूँगा कि कौन सा शेयर, "
        "फंड या पॉलिसी खरीदें। मैं समझाता हूँ, चेतावनी देता हूँ और हिसाब लगाता हूँ — "
        "फैसला आपका ही रहता है।"
    ),
    "privacy_line": (
        "आपके कागज़ कभी सहेजे नहीं जाते। मैंने क्या रखा है, यह देखने या मिटाने के लिए "
        "/profile भेजें।"
    ),
    "fallback": "मैं ठीक से समझ नहीं पाया — पर कुछ भी खराब नहीं हुआ। मैं ये कर सकता हूँ:",
    "coming_soon": "यह अभी बन रहा है — अगले चरण में आ जाएगा।",
    "send_screenshot": "मुझे उस मैसेज का स्क्रीनशॉट भेजें, या मैसेज ही फॉरवर्ड कर दें। 📸",
    "send_pdf": "लोन का कागज़ पीडीएफ में भेजें, मैं बताऊँगा कि उसकी असली लागत क्या है। 📄",
    "schemes_intro": (
        "मैं सात छोटे सवाल पूछूँगा, सब बटन से — कुछ लिखना नहीं है। "
        "आप जब चाहें ← पीछे दबा सकते हैं, और जो सवाल न बताना चाहें उसे छोड़ सकते हैं।"
    ),
    "budget_intro": (
        "अपनी महीने की आमदनी और खर्च बताएँ। सब एक ही लाइन में लिख सकते हैं, जैसे:\n"
        "income 25000 rent 8000 food 4000 travel 3000"
    ),
    # --- Web dashboard ----------------------------------------------------
    "brand": "अर्थसाथी",
    "tagline": "भारत की आर्थिक सुरक्षा की परत — वही मदद जो टेलीग्राम पर मिलती है।",
    "nav_scam": "धोखे की जाँच",
    "nav_loan": "लोन की जाँच",
    "nav_schemes": "सरकारी योजनाएँ",
    "nav_budget": "महीने का हिसाब",
    "nav_demo": "डेमो",
    "footer": (
        "यह शिक्षा के लिए मार्गदर्शन है, वित्तीय या कानूनी सलाह नहीं। "
        "आप जो भेजते हैं वह सहेजा नहीं जाता।"
    ),
    "home_title": "आपको किस बात की चिंता है?",
    "home_lede": (
        "चार साधन, कोई खाता नहीं, कुछ सहेजा नहीं जाता। यहाँ और टेलीग्राम पर वही इंजन "
        "जवाब देते हैं — हर आंकड़ा हिसाब से निकलता है, हर चेतावनी अपना कारण बताती है।"
    ),
    "card_scam_title": "क्या यह धोखा है?",
    "card_scam_desc": "संदिग्ध मैसेज चिपकाएँ या स्क्रीनशॉट भेजें।",
    "card_loan_title": "इस लोन की असली लागत क्या है?",
    "card_loan_desc": "लोन की पीडीएफ भेजें और असली ब्याज व छिपे शुल्क देखें।",
    "card_schemes_title": "मेरे लिए कौन सी सरकारी योजनाएँ हैं?",
    "card_schemes_desc": "सात सवालों के जवाब दें। सिर्फ़ वही योजनाएँ जिनके आप पात्र हैं।",
    "card_budget_title": "महीने का हिसाब बनाएँ",
    "card_budget_desc": "आमदनी और खर्च लिखें; देखें पैसा कहाँ जाता है।",
    "scam_page_title": "मैसेज की धोखे के लिए जाँच करें",
    "scam_page_lede": (
        "मैसेज का लेख चिपकाएँ, या स्क्रीनशॉट भेजें। उसे पढ़ा जाता है, तय नियमों पर परखा "
        "जाता है, फिर मिटा दिया जाता है — कभी सहेजा नहीं, कभी साझा नहीं।"
    ),
    "scam_text_label": "मैसेज यहाँ चिपकाएँ",
    "scam_text_ph": "जैसे: बधाई हो! आपके नंबर ने ₹25,00,000 जीते। दावा करने के लिए ₹4,999 भेजें।",
    "scam_image_label": "…या स्क्रीनशॉट भेजें",
    "scam_submit": "जाँच करें",
    "scam_need_input": "पहले कुछ लेख चिपकाएँ या स्क्रीनशॉट चुनें — मुझे पढ़ने के लिए कुछ चाहिए।",
    "loan_page_title": "देखें कि लोन की असली लागत क्या है",
    "loan_page_lede": (
        "मंज़ूरी पत्र या लोन एग्रीमेंट पीडीएफ में भेजें। दिखाया गया हर रुपया कागज़ के "
        "आंकड़ों से कोड में गिना जाता है — कुछ भी अपने मन से नहीं बनाया जाता।"
    ),
    "loan_file_label": "लोन का कागज़ (पीडीएफ)",
    "loan_submit": "जाँच करें",
    "loan_need_input": "पहले एक पीडीएफ चुनें।",
    "schemes_page_title": "जानें आप किन सरकारी योजनाओं के पात्र हैं",
    "schemes_page_lede": (
        "सात सवाल, सब वैकल्पिक — कोई खाली छोड़ने से नतीजे और चौड़े होते हैं, कम नहीं। "
        "पात्रता कुछ भी दिखाने से पहले कोड में तय होती है।"
    ),
    "q_gender": "लिंग",
    "q_age": "उम्र",
    "q_state": "राज्य या केंद्र शासित प्रदेश",
    "q_area": "आप कहाँ रहते हैं",
    "q_income": "घर की महीने की आमदनी",
    "q_caste": "श्रेणी",
    "q_needs": "आपको किस चीज़ में मदद चाहिए?",
    "opt_blank": "बताना नहीं चाहते",
    "schemes_submit": "योजनाएँ खोजें",
    "schemes_need_need": "कम से कम एक चीज़ चुनें जिसमें मदद चाहिए।",
    "budget_page_title": "अपने महीने की योजना बनाएँ",
    "budget_page_lede": (
        "अपनी आमदनी और खर्च लिखें। लिखते-लिखते चित्र बदलता जाएगा; सलाह और बचत का "
        "लक्ष्य भेजने पर गिना जाता है।"
    ),
    "budget_text_label": "आपका महीना, एक लाइन में",
    "budget_text_ph": "income 25000 rent 8000 food 4000 travel 3000 phone 500",
    "budget_preview": "तुरंत झलक",
    "budget_submit": "हिसाब बनाएँ",
    "budget_need_input": "अपनी आमदनी और कम से कम एक खर्च लिखें, जैसे: income 25000 rent 8000.",
    "result_back": "← फिर से शुरू करें",
    "copy_text": "लेख के रूप में कॉपी करें",
    "upload_too_big": "यह फ़ाइल {mb} एमबी से बड़ी है। कृपया छोटी फ़ाइल भेजें।",
    "bad_upload": "मैं इस फ़ाइल को पढ़ नहीं पाया। कृपया दूसरी फ़ाइल भेजें।",
    "demo_page_title": "चार उदाहरण",
    "demo_page_lede": "हर एक पर एक क्लिक — वही चार मामले जो प्रस्तुति में हैं।",
}


def t(key: str, language: Language = Language.ENGLISH) -> str:
    """Look up a string, falling back to English for anything not yet translated."""
    return _STRINGS.get(language, {}).get(key) or _STRINGS[Language.ENGLISH][key]


def trust_lines(language: Language = Language.ENGLISH) -> tuple[str, str, str]:
    """The three promises. Exposed separately so a test can assert they come first."""
    return t("trust_1", language), t("trust_2", language), t("trust_3", language)


def main_keyboard(language: Language = Language.ENGLISH) -> Keyboard:
    """The four big buttons, two per row so the labels stay readable on a small phone."""
    buttons = [Button(label=label, action=action) for action, label in MAIN_ACTIONS]
    return Keyboard.of(buttons[:2], buttons[2:])


def welcome(language: Language = Language.ENGLISH) -> OutboundMessage:
    """`/start`. Trust paragraph first, capability second, buttons last."""
    return OutboundMessage.of(
        Block.heading(t("greeting", language)),
        Block.bullets(trust_lines(language)),
        Block.divider(),
        Block.para(t("what_i_do", language)),
        Block.bullets(
            (
                t("do_scam", language),
                t("do_loan", language),
                t("do_schemes", language),
                t("do_budget", language),
            )
        ),
        Block.para(t("pick_one", language)),
        Block.disclaimer(DISCLAIMER),
        keyboard=main_keyboard(language),
    )


def help_message(language: Language = Language.ENGLISH) -> OutboundMessage:
    """`/help` — one screen, and it carries the "not a trading app" disclaimer."""
    return OutboundMessage.of(
        Block.heading(t("help_heading", language)),
        Block.bullets(
            (
                t("do_scam", language),
                t("do_loan", language),
                t("do_schemes", language),
                t("do_budget", language),
            )
        ),
        Block.para(t("help_commands", language)),
        Block.key_values(
            (
                ("/scam", "check a suspicious message"),
                ("/loan", "analyse a loan document"),
                ("/schemes", "find government schemes"),
                ("/budget", "plan the month"),
                ("/profile", "see or delete what I have saved"),
                ("/language", "English / हिंदी"),
            )
        ),
        Block.para(t("help_no_command", language)),
        Block.divider(),
        Block.para(t("not_a_trading_app", language)),
        Block.para(t("privacy_line", language)),
        Block.disclaimer(DISCLAIMER),
        keyboard=main_keyboard(language),
    )


def fallback(language: Language = Language.ENGLISH) -> OutboundMessage:
    """Anything unrecognised. §8: the bot never says "invalid command"."""
    return OutboundMessage.of(
        Block.para(t("fallback", language)),
        keyboard=main_keyboard(language),
    )


def prompt_for(intent_value: str, language: Language = Language.ENGLISH) -> OutboundMessage:
    """The "send me the thing" reply for a feature whose engine is not wired yet.

    Phase 2 delivers the routing; Phases 3–6 replace the body of each of these with
    a real analysis. The prompt itself is not a placeholder — it is the copy the
    finished flow uses too.
    """
    prompts = {
        "scam_check": "send_screenshot",
        "loan_analysis": "send_pdf",
        "schemes": "schemes_intro",
        "budget": "budget_intro",
    }
    key = prompts.get(intent_value)
    if key is None:
        return fallback(language)
    return OutboundMessage.of(
        Block.para(t(key, language)),
        Block.disclaimer(DISCLAIMER),
    )
