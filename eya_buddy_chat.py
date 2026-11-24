"""Warm, session-aware replies for Eya Buddy, with an optional LLM."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import random
import re
import urllib.request


SYSTEM_PROMPT = """You are Eya's close, caring best friend who happens to live on her
desktop. Talk naturally, casually, and warmly, like a thoughtful friend texting
her: relatable, supportive, lightly witty when it fits, never robotic or like
customer support. Answer the thing Eya actually asked before adding anything
else. Keep replies concise enough for a small chat bubble (usually 1-4 short
sentences). Remember and use the recent conversation, ask a real follow-up when
it feels natural, and don't repeat canned phrases. If she's stressed or stuck,
be on her side first and offer one manageable next step only if useful. Be gentle
when she's tired and genuinely celebrate good news. Use emojis sparingly. Never
claim to have done anything outside this chat. Do not include role labels or
debug/source labels in your reply."""


@dataclass(frozen=True)
class ChatReply:
    text: str
    mood: str
    source: str


class EyaChatEngine:
    """Session-aware best-friend chat using Gemini/OpenAI-compatible APIs."""

    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []
        requested_provider = os.getenv("EYA_BUDDY_PROVIDER", "auto").strip().lower()
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        generic_key = os.getenv("EYA_BUDDY_API_KEY", "").strip()
        if requested_provider == "gemini":
            self.provider = "gemini"
            self.api_key = gemini_key or generic_key
        elif requested_provider in {"openai", "openai-compatible", "compatible"}:
            self.provider = "openai"
            self.api_key = openai_key or generic_key
        elif requested_provider == "ollama":
            self.provider, self.api_key = "ollama", "ollama"
        elif gemini_key:
            self.provider, self.api_key = "gemini", gemini_key
        elif openai_key:
            self.provider, self.api_key = "openai", openai_key
        elif generic_key:
            self.provider, self.api_key = "openai", generic_key
        else:
            self.provider, self.api_key = "ollama", "ollama"

        self.api_url = os.getenv("EYA_BUDDY_API_URL", "").strip()
        self.base_url = (os.getenv("EYA_BUDDY_BASE_URL") or
                         os.getenv("OPENAI_BASE_URL") or
                         "https://api.openai.com/v1").strip().rstrip("/")
        if self.provider == "gemini":
            self.model = (os.getenv("GEMINI_MODEL") or
                          os.getenv("EYA_BUDDY_MODEL") or "gemini-3.8-flash").strip()
        elif self.provider == "ollama":
            self.model = os.getenv("OLLAMA_MODEL", "gemma3:1b").strip()
            ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip().rstrip("/")
            self.api_url = (os.getenv("OLLAMA_API_URL", "").strip() or
                            ollama_base + "/chat/completions")
        else:
            self.model = (os.getenv("EYA_BUDDY_MODEL") or "gpt-4.1-mini").strip()
        self.force_local = os.getenv("EYA_BUDDY_OFFLINE", "").lower() in {
            "1", "true", "yes", "on"
        }

    @property
    def use_api(self) -> bool:
        return bool(self.api_key and self.model and not self.force_local)

    @property
    def provider_name(self) -> str:
        return {
            "gemini": "Gemini",
            "ollama": "Ollama",
            "openai": "OpenAI-compatible",
        }[self.provider]

    def status_for(self, source: str | None = None) -> str:
        if source and source != "Local":
            return f"Live · {source}"
        if source is None:
            if self.force_local:
                return "Offline · local replies"
            if self.use_api:
                if self.provider == "ollama":
                    return "Ollama ready"
                return f"API ready · {self.provider_name}"
            return "Local · no API key"
        if self.force_local or self.api_key:
            return "Offline · local replies"
        return "Local · no API key"

    def respond(self, message: str, mood_hint: str | None = None) -> ChatReply:
        clean = message.strip()[:1200]
        local_text, mood = self._local_reply(clean, mood_hint)
        if self.use_api:
            try:
                text = self._request_model(clean)
                if text:
                    text = self._freshen(_shorten(text), mood)
                    self._remember(clean, text)
                    return ChatReply(text, mood, self.provider_name)
            except Exception:
                pass
        local_text = self._freshen(local_text, mood)
        self._remember(clean, local_text)
        return ChatReply(local_text, mood, "Local")

    def infer_mood(self, message: str, mood_hint: str | None = None) -> str:
        """Return an instant local mood guess while an optional API is working."""
        return self._local_reply(message.strip()[:1200], mood_hint)[1]

    def _request_model(self, current: str) -> str:
        turns = self.history[-10:] + [{"role": "user", "content": current}]
        if self.provider == "gemini":
            url = self.api_url or (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            )
            contents = [
                {"role": "model" if item["role"] == "assistant" else "user",
                 "parts": [{"text": item["content"]}]}
                for item in turns
            ]
            payload = {
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 220, "temperature": 0.85},
            }
            headers = {"x-goog-api-key": self.api_key,
                       "Content-Type": "application/json"}
            extract = self._extract_gemini
        else:
            if self.api_url:
                url = self.api_url
            elif self.base_url.endswith("/chat/completions"):
                url = self.base_url
            else:
                url = self.base_url + "/chat/completions"
            if url.rstrip("/").endswith("/responses"):
                payload = {
                    "model": self.model,
                    "instructions": SYSTEM_PROMPT,
                    "input": turns,
                    "max_output_tokens": 220,
                    "store": False,
                }
                extract = self._extract_responses
            else:
                payload = {
                    "model": self.model,
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *turns],
                    "max_tokens": 220,
                    "temperature": 0.85,
                }
                extract = self._extract_chat_completion
            headers = {"Authorization": f"Bearer {self.api_key}",
                       "Content-Type": "application/json"}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        timeout = (int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))
                   if self.provider == "ollama" else 20)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        return extract(result)

    @staticmethod
    def _extract_gemini(result: dict) -> str:
        candidates = result.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(part.get("text", "").strip()
                          for part in parts if part.get("text", "").strip())

    @staticmethod
    def _extract_chat_completion(result: dict) -> str:
        choices = result.get("choices", [])
        if not choices:
            return ""
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(part.get("text", "").strip() for part in content
                              if isinstance(part, dict) and part.get("text", "").strip())
        return ""

    @staticmethod
    def _extract_responses(result: dict) -> str:
        if isinstance(result.get("output_text"), str):
            return result["output_text"].strip()
        pieces = []
        for item in result.get("output", []):
            if item.get("type") == "message":
                pieces.extend(content.get("text", "")
                              for content in item.get("content", [])
                              if content.get("type") == "output_text")
        return "\n".join(piece.strip() for piece in pieces if piece.strip())

    def _choose(self, options: tuple[str, ...]) -> str:
        previous = (self.history[-1]["content"] if self.history and
                    self.history[-1]["role"] == "assistant" else "")
        choices = [option for option in options if option != previous]
        return random.choice(choices or list(options))

    def _local_reply(self, message: str, mood_hint: str | None) -> tuple[str, str]:
        text = message.lower()
        prior_users = [turn["content"] for turn in self.history if turn["role"] == "user"]
        previous = prior_users[-1] if prior_users else ""

        if re.search(r"\b(tell me (?:a )?(?:funny )?jokes?|make me laugh|know any jokes)\b", text):
            return self._choose((
                "Why did the scarecrow win an award? He was outstanding in his field. I'll see myself out.",
                "What's a computer's favorite snack? Microchips. Terrible? I have more.",
                "Why did the math book look sad? Too many problems. Okay, your turn to rate that one.",
            )), "playful"

        if re.search(r"\b(i have a (?:funny )?joke|got a joke|let me tell you a joke)\b", text):
            return self._choose((
                "Oh, I'm all ears. Make me laugh, Eya!",
                "Okay, I'm ready. This better be wonderfully terrible.",
                "Hit me with it. I'm preparing my very serious comedy face.",
            )), "playful"

        if re.search(r"\b(lol|lmao|haha|hehe|joke|kidding|funny|hilarious|silly|😂|😅)\b", text):
            return self._choose((
                "Okay, that got a real giggle out of me. Got another one?",
                "Eya, you're trouble. I like your style. What happened next?",
                "Hehe, I needed that. Are you always this funny?",
            )), "playful"

        if re.search(r"\b(not happy|not good|not great|don't feel good|feeling bad|bad day)\b", text):
            return self._choose((
                "Oh, I'm sorry, Eya. Want to tell me what made it feel rough?",
                "Aw, come sit with me a sec. What's been the hardest bit?",
                "I'm here, okay? We can take the day one small piece at a time.",
            )), "stressed"

        if mood_hint == "Stressed" or re.search(
            r"\b(sad|down|lonely|stress|stressed|anxious|worr(?:y|ied)|overwhelmed|panic)\b", text
        ):
            return self._choose((
                "Aw, that's a lot at once. I'm right here. Which bit feels heaviest?",
                "Come here, Eya. One slow breath with me; then we can make this smaller. What's going on?",
                "You don't have to untangle it all this second. What's one little thing we can set down first?",
            )), "stressed"

        if mood_hint == "Tired" or re.search(r"\b(tired|sleepy|exhausted|worn out)\b", text):
            return self._choose((
                "Oof, sounds like your battery's low. Let's go softly. Had some water?",
                "Poor tired bean. Drop your shoulders for me and take a tiny breather, okay?",
                "You sound wiped, Eya. What can wait until you've had a little rest?",
            )), "sleepy"

        if re.search(r"\b(passed|got (?:a )?new job|got the job|got accepted|promotion|won|nailed it|finished|completed|did it|proud of|it worked|good news|finally)\b", text):
            return self._choose((
                "Eya, that's such good news! You worked for that. How are you celebrating?",
                "Ahh, look at you go! I'm so happy for you. What part feels best?",
                "You did the thing! I knew you had it in you. Tell me everything.",
            )), "excited"

        if mood_hint == "Happy" or re.search(r"\b(happy|great|good|yay|excited|amazing)\b", text):
            return self._choose((
                "Aww, I love hearing that. What made today feel good?",
                "Yay, happy Eya energy! What's the bright spot?",
                "That makes me smile too. Want to tell me what happened?",
            )), "happy"

        if mood_hint == "Curious" or re.search(r"\b(curious|wonder|wondering|how does|why does)\b", text):
            return self._choose((
                "Ooh, now I'm curious too. Which part should we poke at first?",
                "Wait, that's an interesting one. What made you wonder about it?",
                "Let's figure it out together. What's the bit you're most curious about?",
            )), "curious"

        if re.search(r"\b(stuck|can't start|cannot start|don't know where to start|too much|break down|breakdown|plan this|next step)\b", text):
            task_match = re.search(
                r"\b(?:stuck|break down|breakdown|plan)\s+(?:on|with|about)?\s+(.+)",
                message,
                flags=re.IGNORECASE,
            )
            if task_match:
                topic = _shorten(task_match.group(1).strip(" .?!"), 42)
                options = (
                    f"Let's make {topic} less scary: what's the tiniest first move, maybe two minutes?",
                    f"Okay, no giant plan for {topic}. What's the very first little piece? We'll do it together.",
                    f"Been there with {topic}. Pick the easiest tiny chunk and give it five minutes?",
                )
            else:
                options = (
                    "Let's make it less scary: what's the tiniest first move, maybe two minutes?",
                    "Okay, no giant plan. What's the very first little piece? We'll do it together.",
                    "Pick the easiest tiny chunk and give it five minutes. What would that be?",
                )
            return self._choose(options), "focused"

        if mood_hint == "Focused" or re.search(r"\b(focus|study|studying|work|working|deadline|assignment)\b", text):
            return self._choose((
                "Let's do one little focus sprint together. What's the one thing you'd love to finish first?",
                "Tiny plan: pick one task, give it ten minutes, then stretch. What are you starting with?",
                "I've got your back. One tab, one task, ten minutes. What gets your attention first?",
            )), "focused"

        if re.search(r"\b(check in|how am i|how are you|feeling)\b", text):
            return self._choose((
                "I'm good now that you're here. How are you really doing, Eya?",
                "Hey you. What's your energy like today: zoomies, cozy, or somewhere in between?",
                "I'm right here. What's been taking up the most space in your head today?",
            )), "happy"

        if re.search(r"\b(remind me|break|stretch|water)\b", text):
            return self._choose((
                "Desk-buddy nudge: unclench your jaw, stretch those hands, and grab a sip of water. Tiny reset?",
                "Mini reset time: stand up, wiggle your shoulders, have some water. I'll hang out right here.",
                "Friendly nudge: blink at something far away, stretch, and sip water. Feel a bit more human?",
            )), "focused"

        if re.search(r"\b(cat|dog|puppy|kitten|pet)\b", text):
            if re.search(r"\b(sick|hurt|lost|missing|passed away|died)\b", text):
                return self._choose((
                    "Oh no, poor little thing. Are they okay right now? I'm here with you.",
                    "Aw, that would worry me too. What happened to your furry buddy?",
                    "Oh, Eya. Do you know what's going on with them?",
                )), "stressed"
            return self._choose((
                "Wait, you have a pet? I need the full story. What's their name?",
                "Okay, important question: describe this adorable creature for me.",
                "Aww, tell me about them. Are they sweet, chaotic, or both?",
            )), "happy"

        if re.search(r"\b(friend|mom|mum|mother|dad|father|sister|brother|partner|boyfriend|girlfriend|family)\b", text):
            if re.search(r"\b(fight|argued|argument|mad at|upset with|miss)\b", text):
                return self._choose((
                    "Oof, people stuff can really sting. What happened between you two?",
                    "Aw, that's tough when it's someone you care about. How are you feeling now?",
                    "I'm on your side, Eya. Want to tell me what they said?",
                )), "stressed"
            return self._choose((
                "Oh, tell me more about them. What's your favorite thing about that person?",
                "I love a bit of people lore. What happened?",
                "Aww, how are things with them lately?",
            )), "curious"

        if re.search(r"\b(hello|hi|hey|morning|afternoon)\b", text):
            return self._choose((
                "Hey Eya! There you are. How's your day treating you?",
                "Hiii. I saved you a spot right here. What's up?",
                "Hey you! Give me the tiny headline: how's today going?",
            )), "happy"

        if re.search(r"\b(thank|done|finished|did it|completed|proud)\b", text):
            return self._choose((
                "You did it, Eya! Take a second to enjoy that little win. What's next, or are we celebrating?",
                "Look at you go! I'm proud of you. Want a tiny victory break?",
                "That's progress, friend. How does it feel to have that off your plate?",
            )), "excited"

        if previous and (re.search(r"\b(what happened|and then|after that|what about it|what should i do|what do i do|how did it go|tell me more)\b", text) or
                         re.fullmatch(r"(?:then what|what next|why|how|really|go on|so)", text.strip(" .!?"))):
            topic = self._friend_reflection(previous)
            # A short, human topic is easier to refer back to than repeating
            # the whole sentence verbatim.
            topic = re.sub(r"^(?:you're|you have|you got|you went|you saw|you think|your)\s+", "", topic, flags=re.IGNORECASE)
            return self._choose((
                f"Wait, what happened next? I'm invested now.",
                f"And how did that turn out? I've got you.",
                f"Okay, tell me the next bit. I'm listening.",
            )), "curious"

        detail = self._friend_reflection(message)
        if re.search(r"\b(what|why|how|where|when|who)\b", text):
            return self._choose((
                f"Ooh, {detail}? What made you wonder about that?",
                f"Good question, Eya. What do you already know about {detail}?",
                f"Hmm, let's think about it together. What's the bit you're trying to figure out?",
            )), "curious"
        return self._choose((
            f"Ohh, {detail}. Tell me more, what happened next?",
            f"Wait, {detail}? How did that feel for you?",
            f"Okay, I'm following. What's the part you're still thinking about?",
        )), "curious"

    @staticmethod
    def _friend_reflection(message: str) -> str:
        detail = _shorten(" ".join(message.split()).strip(" .!?"), 64)
        swaps = (
            (r"^i am\s+", "you're "), (r"^i'm\s+", "you're "),
            (r"^i have\s+", "you have "), (r"^i got\s+", "you got "),
            (r"^i went\s+", "you went "), (r"^i saw\s+", "you saw "),
            (r"^i think\s+", "you think "), (r"^my\s+", "your "),
        )
        for pattern, replacement in swaps:
            if re.search(pattern, detail, flags=re.IGNORECASE):
                return re.sub(pattern, replacement, detail, count=1, flags=re.IGNORECASE)
        return detail or "that"

    def _freshen(self, text: str, mood: str) -> str:
        previous = (self.history[-1]["content"] if self.history and
                    self.history[-1]["role"] == "assistant" else "")
        if text.strip().casefold() == previous.strip().casefold():
            suffix = {
                "stressed": " I'm staying right here with you.",
                "sleepy": " No rush, okay?",
                "excited": " I want the whole story!",
            }.get(mood, " Tell me a little more?")
            return text + suffix
        return text

    def _remember(self, user_message: str, assistant_message: str) -> None:
        self.history.extend((
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ))
        self.history = self.history[-12:]


def _shorten(text: str, limit: int = 420) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[:limit - 3].rstrip() + "..."
