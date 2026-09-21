# MiniMax H3 serverless worker: FROM the same image the H3 pod runs, + baked handler.
FROM hearmeman/comfyui-minimax-template:v9
RUN python3 -m pip install --no-cache-dir runpod
RUN mkdir -p /opt/rp
COPY handler.py /opt/rp/handler.py
COPY start.sh   /opt/rp/start.sh
RUN chmod +x /opt/rp/start.sh
ENV I2V_POLL_TIMEOUT_S=900 \
    I2V_MAX_INLINE_MB=25 \
    I2V_CLEANUP=1 \
    COMFY_BOOT_TIMEOUT_S=600 \
    COMFY_HOST=127.0.0.1:8188
CMD ["/opt/rp/start.sh"]
