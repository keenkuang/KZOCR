"""
UniRec Adapter for TCM OCR System.

Uses UniRec 0.1B ONNX model (encoder-decoder) for line-level text recognition.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Default paths — override via env UNIREC_MODEL_DIR
_DEFAULT_MODEL_DIR = "/home/keen/unirec_0_1b_onnx"

# Special token IDs
TOKEN_BOS = 0
TOKEN_PAD = 1
TOKEN_EOS = 2
TOKEN_UNK = 3
TOKEN_VISION_PAD = 4

# Max decoding steps
_MAX_DECODE_STEPS = 256


class UniRecAdapter:
    """Adapter for UniRec 0.1B ONNX recognition engine.

    Provides line-level text recognition using an encoder-decoder
    transformer model with ONNX Runtime.

    Attributes:
        model_dir (str): Path to the UniRec model directory.
        engine: The underlying ONNX InferenceSession (encoder).
        decoder: The underlying ONNX InferenceSession (decoder).
        id_to_token (dict): Token ID to character mapping.
        token_to_id (dict): Character to token ID mapping.
        _closed (bool): Flag indicating if adapter has been closed.
    """

    def __init__(
        self,
        char_dict: Optional[Dict[str, str]] = None,
        device: str = 'cpu',
    ) -> None:
        """Initialize the UniRec adapter.

        Args:
            char_dict: Custom character dictionary (unused, kept for compat).
            device: Computing device ('cpu').
        """
        self.model_dir: str = Path(_DEFAULT_MODEL_DIR)
        self.encoder: Any = None
        self.decoder: Any = None
        self.id_to_token: Dict[int, str] = {}
        self._closed: bool = False
        self._init_engine()

    def _init_engine(self) -> None:
        """Load UniRec ONNX models and tokenizer mapping."""
        try:
            import onnxruntime as ort

            enc_path = self.model_dir / "unirec_encoder.onnx"
            dec_path = self.model_dir / "unirec_decoder.onnx"
            map_path = self.model_dir / "unirec_tokenizer_mapping.json"

            if not enc_path.exists():
                raise FileNotFoundError(f"UniRec encoder not found: {enc_path}")
            if not dec_path.exists():
                raise FileNotFoundError(f"UniRec decoder not found: {dec_path}")

            self.encoder = ort.InferenceSession(str(enc_path), providers=["CPUExecutionProvider"])
            self.decoder = ort.InferenceSession(str(dec_path), providers=["CPUExecutionProvider"])

            with open(map_path) as f:
                mapping = json.load(f)
            self.id_to_token = {int(k): v for k, v in mapping["id_to_token"].items()}

            logger.info(
                "UniRec initialized (vocab=%d, encoder=%s, decoder=%s)",
                mapping["vocab_size"], enc_path.name, dec_path.name,
            )
        except Exception as e:
            logger.error("Failed to initialize UniRec: %s", e)
            raise

    def _preprocess(self, line_img: np.ndarray) -> np.ndarray:
        """Preprocess a line image for the encoder.

        The UniRec ViT encoder accepts a *variable* spatial size (its input is
        ``[batch, 3, height, width]`` with symbolic dims). It must NOT be
        force-resized to a square 224x224: squashing a wide text line into a
        square destroys the character layout, the encoder then emits near-garbage
        features, and the autoregressive decoder collapses into a degenerate
        repetition loop ("三农模式, 通过三农模式...") that never emits EOS.

        So we only:
          * convert BGR -> RGB (cv2.imread returns BGR),
          * optionally shrink oversized images while keeping aspect ratio,
          * apply ImageNet mean/std normalisation.

        Args:
            line_img: Cropped line image (H, W) or (H, W, C) as a BGR np.ndarray
                (the format produced by cv2.imread).

        Returns:
            Preprocessed tensor (1, 3, H, W) ready for the encoder.
        """
        import cv2

        # cv2.imread yields BGR (or BGRA). Convert to RGB.
        if line_img.ndim == 2:
            img = cv2.cvtColor(line_img, cv2.COLOR_GRAY2RGB)
        elif line_img.shape[2] == 4:
            img = cv2.cvtColor(line_img, cv2.COLOR_BGRA2RGB)
        else:
            img = cv2.cvtColor(line_img, cv2.COLOR_BGR2RGB)

        # Defensive: cap pathologically large images by shrinking while keeping
        # the aspect ratio, so we never OOM. Benchmark line images are small and
        # pass through untouched (native resolution gives the best accuracy).
        h, w = img.shape[:2]
        max_side = 1024
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            img = cv2.resize(
                img, (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_LINEAR,
            )

        # Normalize to [0,1], then ImageNet mean/std
        img = img.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        img = (img - mean) / std

        # CHW format + batch dim
        img = img.transpose(2, 0, 1)[np.newaxis, ...]
        return img

    def _decode_tokens(self, token_ids: List[int]) -> str:
        """Convert token IDs to text string."""
        chars = []
        for tid in token_ids:
            if tid in (TOKEN_BOS, TOKEN_PAD, TOKEN_VISION_PAD):
                continue
            if tid == TOKEN_EOS:
                break
            ch = self.id_to_token.get(tid, "")
            if ch:
                chars.append(ch)
        return "".join(chars).replace("Ġ", " ").replace("Ċ", "\n")

    def recognize(self, line_img: np.ndarray) -> str:
        """Recognize a single line image and return the full text.

        Args:
            line_img: Cropped line image as numpy array (H, W) or (H, W, C).

        Returns:
            Recognized text string.
        """
        if self._closed:
            raise RuntimeError("UniRecAdapter has been closed.")

        start_time = time.time()
        result_text = ""

        try:
            # Preprocess
            pixel_values = self._preprocess(line_img)

            # Run encoder
            enc_out = self.encoder.run(
                ["encoder_hidden_states", "cross_k", "cross_v"],
                {"pixel_values": pixel_values},
            )
            enc_hidden, cross_k, cross_v = enc_out
            enc_hidden.shape[1]

            # Prepare decoder state
            past_initial = np.zeros((1, 6, 0, 128), dtype=np.float32)

            # Initialize with BOS token
            input_ids = np.array([[TOKEN_BOS]], dtype=np.int64)
            position_ids = np.array([[0]], dtype=np.int64)

            decoded_ids = []
            past_keys = None
            past_values = None
            for step in range(_MAX_DECODE_STEPS):
                dec_input = {
                    "input_ids": input_ids,
                    "position_ids": position_ids,
                    "cross_k": cross_k,
                    "cross_v": cross_v,
                }
                for i in range(6):
                    key_name = f"past_key_{i}"
                    val_name = f"past_value_{i}"
                    if step == 0:
                        dec_input[key_name] = past_initial
                        dec_input[val_name] = past_initial
                    else:
                        dec_input[key_name] = past_keys[i]
                        dec_input[val_name] = past_values[i]

                dec_out = self.decoder.run(
                    ["logits", *(f"present_key_{i}" for i in range(6)),
                     *(f"present_value_{i}" for i in range(6))],
                    dec_input,
                )

                logits = dec_out[0]
                past_keys = dec_out[1:7]
                past_values = dec_out[7:13]

                next_token = np.argmax(logits[0, -1, :])
                token_id = int(next_token)

                if token_id == TOKEN_EOS:
                    break

                decoded_ids.append(token_id)

                # Prepare next step input
                input_ids = np.array([[token_id]], dtype=np.int64)
                position_ids = np.array([[step + 1]], dtype=np.int64)

            result_text = self._decode_tokens(decoded_ids)

        except Exception as e:
            logger.error("UniRec recognition failed: %s", e)
            result_text = ""

        elapsed = time.time() - start_time
        logger.debug("UniRec recognized in %.3fs: '%s'", elapsed, result_text)
        return result_text

    def close(self) -> None:
        """Release UniRec resources."""
        if self._closed:
            return
        try:
            self.encoder = None
            self.decoder = None
            logger.info("UniRec adapter closed successfully")
        except Exception as e:
            logger.error("Error closing UniRec adapter: %s", e)
        finally:
            self._closed = True

    def __del__(self) -> None:
        if not self._closed:
            self.close()

    def __enter__(self) -> 'UniRecAdapter':
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: None) -> None:
        self.close()
