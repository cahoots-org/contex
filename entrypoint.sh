#!/bin/bash
set -e

echo "============================================================"
echo "Context Engine Service - Startup"
echo "============================================================"

# Configuration
FINETUNED_MODEL_DIR="/app/qwen-0.5b-context-json"
MODEL_NAME="${CONTEXT_ENGINE_MODEL:-Qwen/Qwen2.5-0.5B-Instruct}"

# Check if we should use fine-tuned model
if [[ "$MODEL_NAME" == *"Qwen"* ]] && [[ "$MODEL_NAME" != "/"* ]] && [[ "$MODEL_NAME" != "."* ]]; then
    # User wants Qwen model (HuggingFace model), check for fine-tuned version
    echo ""
    echo "[Startup] Checking for fine-tuned model..."

    if [ -d "$FINETUNED_MODEL_DIR" ] && [ -f "$FINETUNED_MODEL_DIR/adapter_config.json" ]; then
        echo "[Startup] ✓ Fine-tuned model found at $FINETUNED_MODEL_DIR"
        echo "[Startup]   Using fine-tuned model for better JSON output"
        export CONTEXT_ENGINE_MODEL="$FINETUNED_MODEL_DIR"
    else
        echo "[Startup] ⚠ Fine-tuned model not found"
        echo "[Startup]   Checking if fine-tuning should run..."

        # Check if FINETUNE_ON_STARTUP is enabled
        if [ "${FINETUNE_ON_STARTUP:-false}" = "true" ]; then
            echo ""
            echo "[Fine-tuning] Starting LoRA fine-tuning of Qwen2.5 0.5B..."
            echo "[Fine-tuning] This will take 1-2 minutes on CPU"
            echo "[Fine-tuning] Output directory: $FINETUNED_MODEL_DIR"
            echo ""

            # Run fine-tuning
            python finetune_gemma.py

            if [ $? -eq 0 ]; then
                echo ""
                echo "[Fine-tuning] ✓ Fine-tuning complete!"
                echo "[Fine-tuning]   Model saved to: $FINETUNED_MODEL_DIR"
                export CONTEXT_ENGINE_MODEL="$FINETUNED_MODEL_DIR"
            else
                echo ""
                echo "[Fine-tuning] ✗ Fine-tuning failed"
                echo "[Fine-tuning]   Falling back to base model: $MODEL_NAME"
            fi
        else
            echo "[Startup]   FINETUNE_ON_STARTUP not enabled"
            echo "[Startup]   Using base model: $MODEL_NAME"
            echo ""
            echo "[Startup] To enable automatic fine-tuning, set:"
            echo "[Startup]   FINETUNE_ON_STARTUP=true"
            echo ""
            echo "[Startup] Or run fine-tuning manually:"
            echo "[Startup]   docker compose exec context-engine python finetune_gemma.py"
        fi
    fi
elif [[ "$MODEL_NAME" == "/"* ]] || [[ "$MODEL_NAME" == "."* ]]; then
    # User specified a path (absolute or relative)
    echo "[Startup] Using model at specified path: $MODEL_NAME"
else
    # User specified a HuggingFace model ID
    echo "[Startup] Using HuggingFace model: $MODEL_NAME"
fi

echo ""
echo "[Startup] Starting Context Engine with model: ${CONTEXT_ENGINE_MODEL:-$MODEL_NAME}"
echo "============================================================"
echo ""

# Start the main application
exec python main.py
