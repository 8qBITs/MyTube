from video_app import create_app

app = create_app()

if __name__ == "__main__":
    # Bind to all interfaces so you can reach it from outside
    app.run(host="0.0.0.0", port=5000, debug=True)