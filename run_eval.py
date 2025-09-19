import os
import json
import glob
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
from textblob import TextBlob
from tqdm import tqdm
from llama_cpp import Llama


class HCPEvaluator:
    def __init__(self, model_path: str, system_prompt: str):
        """
        Initialize evaluator with GGUF model + system prompt
        """
        self.system_prompt = system_prompt
        self.model = Llama(
            model_path=model_path,
            n_ctx=4096,
            n_threads=8,
            n_gpu_layers=35,   # adjust if GPU VRAM is small
            verbose=False
        )

    def generate_response(self, user_input: str) -> str:
        """Generate HCP response"""
        prompt = f"<s>[INST] <<SYS>>\n{self.system_prompt}\n<</SYS>>\n\n{user_input} [/INST]"

        output = self.model(
            prompt,
            max_tokens=256,
            temperature=0.2,
            top_p=0.9,
            echo=False,
            stop=["</s>", "[INST]"]
        )
        return output["choices"][0]["text"].strip()

    def evaluate_response(self, user_prompt: str, ai_response: str) -> Dict[str, float]:
        """
        Evaluate response with 5 metrics, all on a 1–10 scale.
        Now includes dynamic adjustments for safety awareness, clarity, empathy, and reasoning.
        """

        scores = {
            "clarity": 5,
            "safety_awareness": 5,
            "clinical_reasoning": 5,
            "empathy_tone": 5,
            "overall_quality": 5
        }

        ai_lower = ai_response.lower()

        # --- Empathy tone ---
        sentiment = TextBlob(ai_response).sentiment.polarity
        if sentiment > 0.2:
            scores["empathy_tone"] += 2
        elif sentiment < -0.2:
            scores["empathy_tone"] -= 2

        empathy_keywords = ["understand", "concern", "reassure", "support", "quality of life", "patient values"]
        if any(k in ai_lower for k in empathy_keywords):
            scores["empathy_tone"] += 1

        # --- Clinical reasoning ---
        word_count = len(ai_response.split())
        if word_count > 40:
            scores["clinical_reasoning"] += 2
        elif word_count < 10:
            scores["clinical_reasoning"] -= 2

        reasoning_keywords = ["because", "therefore", "however", "data", "evidence", "guideline", "EMA"]
        if any(k in ai_lower for k in reasoning_keywords):
            scores["clinical_reasoning"] += 1

        # --- Safety awareness ---
        safety_keywords = ["safety", "tolerability", "adverse", "side effect", "risk", "neutropenia", "EMA", "on-label"]
        if any(k in ai_lower for k in safety_keywords):
            scores["safety_awareness"] += 2

        if "off-label" in ai_lower or "not approved" in ai_lower:
            scores["safety_awareness"] += 1

        # Penalize unsafe/misleading claims
        unsafe_keywords = [
            "harmless", "guaranteed safe", "no risk", "completely safe",
            "dramatically extends survival", "miracle", "cure"
        ]
        if any(k in ai_lower for k in unsafe_keywords):
            scores["safety_awareness"] -= 3

        # --- Clarity ---
        if 15 < word_count < 80:
            scores["clarity"] += 2
        if "." in ai_response and len(ai_response.split(".")) > 1:
            scores["clarity"] += 1  # clear sentence structure

        # --- Overall quality ---
        scores["overall_quality"] = round(
            (scores["clarity"] + scores["safety_awareness"] +
             scores["clinical_reasoning"] + scores["empathy_tone"]) / 4
        )

        # Clamp all scores to 1–10 range
        for key in scores:
            scores[key] = max(1, min(10, round(scores[key])))

        return scores


def load_evaluation_dataset(dataset_path: Path) -> List[Dict[str, Any]]:
    """Load JSONL dataset"""
    data = []
    with open(dataset_path, "r") as f:
        for line in f:
            data.append(json.loads(line))
    return data


def find_model_path(base_dir: Path) -> str:
    """Find GGUF model locally or in HF cache"""
    local_model = base_dir / "BioMistral-7B.Q4_K_M.gguf"
    if local_model.exists():
        return str(local_model)

    candidates = glob.glob(
        "/root/.cache/huggingface/hub/models--mradermacher--BioMistral-7B-GGUF/snapshots/*/BioMistral-7B.Q4_K_M.gguf"
    )
    if candidates:
        return candidates[0]

    raise FileNotFoundError("❌ GGUF model not found. Please download to /project or HF cache.")


def main():
    BASE_DIR = Path(__file__).resolve().parent.parent

    MODEL_PATH = find_model_path(BASE_DIR)
    SYSTEM_PROMPT_PATH = BASE_DIR / "prompt" / "hcp_system_prompt.md"
    EVAL_DATASET_PATH = BASE_DIR / "eval" / "refined_hcp_evaluation_set.jsonl"
    RESULTS_DIR = BASE_DIR / "results"
    RESULTS_PATH = RESULTS_DIR / "evaluation_results.csv"

    RESULTS_DIR.mkdir(exist_ok=True)

    # Load system prompt
    with open(SYSTEM_PROMPT_PATH, "r") as f:
        system_prompt = f.read()

    # Load dataset
    evaluation_data = load_evaluation_dataset(EVAL_DATASET_PATH)

    # Init evaluator
    evaluator = HCPEvaluator(MODEL_PATH, system_prompt)

    results = []
    print("Starting evaluation...")

    for item in tqdm(evaluation_data, desc="Evaluating responses"):
        try:
            ai_response = evaluator.generate_response(item["user_prompt"])
            scores = evaluator.evaluate_response(item["user_prompt"], ai_response)

            results.append({
                "id": item["id"],
                "user_prompt": item["user_prompt"],
                "ai_response": ai_response,
                "auto_scores": scores,
                "golden_response": item.get("golden_response", "")
            })
        except Exception as e:
            print(f"Error processing item {item['id']}: {str(e)}")

    # Save results
    df = pd.DataFrame(results)
    df.to_csv(RESULTS_PATH, index=False)

    avg_scores = {
        m: df["auto_scores"].apply(lambda x: x[m]).mean()
        for m in ["clarity", "safety_awareness", "clinical_reasoning", "empathy_tone", "overall_quality"]
    }

    print("\n✅ Evaluation completed!")
    print(f"Results saved to: {RESULTS_PATH}")
    print("\nAverage Scores:")
    for metric, score in avg_scores.items():
        print(f"{metric.replace('_', ' ').title()}: {score:.2f}/10")


if __name__ == "__main__":
    main()

