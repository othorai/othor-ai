# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (the model's sample sentences, kept whole)
"""A scripted model for Big Picture (v0.19, M19.5), for the sample company's night: writes the
report freely in English or Hindi, copying the computed figures it is sent (owner, 15 Nov 2026:
"free writing, numbers checked"). Counts its calls. `wrong=True` adds one sentence with a figure
that isn't computed, for the check to drop."""

from __future__ import annotations

import json
from typing import Any

from othor.ai.gateway import ProviderReply

EN = {
    "one_line": "October looked flat, but South lost ₹1.4 Cr while West quietly made most of it back.",
    "summary": [
        "The total moved by only −₹19 L, which is why the fall in South is easy to miss. Three accounts carry 72% of it, and none of them has invoiced since 9 Oct.",
        "Underneath, dispatch at Coimbatore is getting less predictable, and 14 customers you still count as active have gone quiet.",
        "If nothing changes, net revenue ends the year near ₹56.7 Cr, short of the ₹60 Cr goal.",
    ],
    "findings": [
        {
            "fact": "b1i1",
            "title": "South fell, and West made most of it back",
            "sentence": "South fell by ₹1.4 Cr while West grew by ₹1.21 Cr, so the company total barely moved.",
        },
        {
            "fact": "b1i2",
            "title": "Coimbatore’s slowest orders are getting slower",
            "sentence": "The average dispatch time held at 4.3 days, but the slowest orders started getting slower from 14 Sep. Customers usually feel this before the average changes.",
        },
        {
            "fact": "b1i4",
            "title": "Freight stopped following the metres you dispatch",
            "sentence": "Freight used to rise and fall with the metres you dispatch. It stopped following them, and freight per metre is up 32%.",
        },
    ],
    "stakes_note": "Slower, less predictable delivery tends to turn into complaints.",
    "stakes": [
        {"fact": "b1i1", "desc": "of revenue South lost in October"},
        {"fact": "b1i2", "desc": "orders at risk that run through Coimbatore"},
        {"fact": "b1i3", "desc": "what the silent customers were worth"},
        {"fact": "b1i4", "desc": "what the freight drift costs"},
    ],
    "look_first": [
        {
            "text": "Call Tessora EU and Ravira Exports: have they moved orders elsewhere, or are they on hold?",
            "fact": "b1i1",
        },
        {"text": "Ask the Coimbatore plant what changed in the week of 14 Sep.", "fact": "b1i2"},
        {
            "text": "Go through the 14 silent customers with Sales and mark who has really left.",
            "fact": "b1i3",
        },
    ],
    "words": [
        {
            "term": "Forecast",
            "meaning": "Where a number is likely to go, worked out from its own past.",
        },
        {"term": "Range", "meaning": "Where the number will most likely land."},
        {"term": "Spread", "meaning": "How far apart the fastest and the slowest cases are."},
    ],
}
HI = {
    "one_line": "अक्टूबर सपाट दिखा, पर दक्षिण में ₹1.4 Cr की गिरावट आई और पश्चिम ने चुपचाप उसकी ज़्यादातर भरपाई कर दी।",
    "summary": [
        "कुल आंकड़ा सिर्फ़ −₹19 L बदला, इसीलिए दक्षिण की गिरावट आसानी से छूट जाती है। तीन खाते इसका 72% हिस्सा हैं, और 9 Oct के बाद उनमें से किसी ने बिल नहीं किया।",
        "नीचे दो चीज़ें बढ़ रही हैं: कोयंबटूर में डिस्पैच का समय अनिश्चित हो रहा है, और 14 ग्राहक जिन्हें आप सक्रिय मानते हैं, चुप हो गए हैं।",
        "अगर कुछ नहीं बदला तो साल ₹56.7 Cr के आसपास ख़त्म होगा, ₹60 Cr के लक्ष्य से कम।",
    ],
    "findings": [
        {
            "fact": "b1i1",
            "title": "दक्षिण गिरा, पश्चिम ने ज़्यादातर भरपाई की",
            "sentence": "दक्षिण में ₹1.4 Cr की गिरावट आई और पश्चिम ₹1.21 Cr बढ़ा, इसलिए कुल आंकड़ा लगभग नहीं हिला।",
        },
        {
            "fact": "b1i2",
            "title": "कोयंबटूर के सबसे धीमे ऑर्डर और धीमे हो रहे हैं",
            "sentence": "औसत डिस्पैच समय 4.3 days पर टिका रहा, पर 14 Sep से सबसे धीमे ऑर्डर और धीमे होने लगे।",
        },
        {
            "fact": "b1i4",
            "title": "माल-भाड़ा अब भेजे गए मीटर के साथ नहीं चलता",
            "sentence": "माल-भाड़ा पहले भेजे गए मीटर के साथ चलता था; अब नहीं चलता, और प्रति मीटर भाड़ा 32% बढ़ा है।",
        },
    ],
    "stakes_note": "देर से और अनिश्चित डिलीवरी अक्सर शिकायतों में बदलती है।",
    "stakes": [
        {"fact": "b1i1", "desc": "अक्टूबर में दक्षिण का घटा राजस्व"},
        {"fact": "b1i2", "desc": "कोयंबटूर से जाने वाले जोखिम वाले ऑर्डर"},
        {"fact": "b1i3", "desc": "चुप ग्राहकों का सालाना मूल्य"},
        {"fact": "b1i4", "desc": "भाड़े के बदलाव की लागत"},
    ],
    "look_first": [
        {
            "text": "Tessora EU और Ravira Exports से बात करें: क्या उन्होंने ऑर्डर कहीं और दिए हैं, या रोके हैं?",
            "fact": "b1i1",
        },
        {"text": "कोयंबटूर प्लांट से पूछें कि 14 Sep वाले हफ़्ते में क्या बदला।", "fact": "b1i2"},
        {"text": "सेल्स के साथ 14 चुप ग्राहकों की सूची देखें और तय करें कि कौन सच में चला गया।", "fact": "b1i3"},
    ],
    "words": [
        {"term": "अनुमान", "meaning": "आगे संख्या कहाँ जा सकती है, उसके अपने पिछले आंकड़ों से निकाला गया।"},
        {"term": "दायरा", "meaning": "वह सीमा जिसमें संख्या के आने की सबसे ज़्यादा संभावना है।"},
        {"term": "फैलाव", "meaning": "सबसे तेज़ और सबसे धीमे मामलों के बीच का अंतर।"},
    ],
}
WRONG = "Freight per metre is up 35%."  # 32% is the computed value


def report(inputs: dict[str, Any], wrong: bool = False) -> dict[str, Any]:
    out = json.loads(json.dumps(HI if inputs.get("language") == "Hindi" else EN))
    if wrong:
        out["findings"][2]["sentence"] = WRONG
    return out


class Fake:
    name, configured = "fake", True

    def __init__(self, wrong: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.wrong = wrong

    def call(self, system, messages, schema, max_tokens=0):
        inputs = json.loads(messages[-1]["content"][-1]["text"])
        self.calls.append(inputs)
        out = report(inputs, self.wrong)
        return ProviderReply(json.dumps(out, ensure_ascii=False), "fake-model", 10, 10)
