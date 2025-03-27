import logging
from PIL import Image

logger = logging.getLogger(__name__)

# Try to import tkinter but don't fail if it's not available
try:
    import tkinter as tk
    from PIL import ImageTk
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False
    logger.warning("Tkinter not available, images will be displayed using PIL's show method")

class MockDisplay:
    """A mock implementation of the Inky display for local development."""
    
    def __init__(self, width=800, height=480, color='black'):
        """Initialize the mock display with given dimensions."""
        self.width = width
        self.height = height
        self.color = color
        self.BLACK = 0
        self.WHITE = 1
        self.RED = 2
        self.YELLOW = 3
        
        self.image = None
        
        # Initialize tkinter UI if available
        if HAS_TKINTER:
            # Start the UI in a separate thread
            import threading
            self.thread = threading.Thread(target=self._init_ui)
            self.thread.daemon = True
            self.root = None
            self.panel = None
            self.tk_image = None
            self.thread.start()
            
    def _init_ui(self):
        """Initialize the Tkinter UI in a separate thread."""
        if not HAS_TKINTER:
            return
            
        self.root = tk.Tk()
        self.root.title("InkyPi Mock Display")
        self.root.geometry(f"{self.width}x{self.height}")
        self.root.configure(background='white')
        
        # Create a blank initial image
        blank_image = Image.new('RGB', (self.width, self.height), color='white')
        self.tk_image = ImageTk.PhotoImage(blank_image)
        
        # Create a label to display the image
        self.panel = tk.Label(self.root, image=self.tk_image)
        self.panel.pack(side="bottom", fill="both", expand="yes")
        
        self.root.mainloop()
    
    def set_image(self, image):
        """Set the image to display."""
        self.image = image
        
    def set_border(self, color):
        """Set the border color of the display."""
        logger.info(f"Setting border color to {color}")
        # No real implementation needed for mock
        pass
        
    def show(self):
        """Display the image on the mock display."""
        if self.image is None:
            logger.warning("No image to display")
            return
            
        # Resize image if needed to fit display
        if self.image.width != self.width or self.image.height != self.height:
            self.image = self.image.resize((self.width, self.height))
        
        # Save the image for reference
        self.image.save('current_display.png')
        logger.info("Saved current display image to current_display.png")
            
        if HAS_TKINTER and self.root is not None:
            # Update the image in the UI thread
            self._update_tkinter_image()
        else:
            # Fall back to PIL's show method
            self.image.show()
    
    def _update_tkinter_image(self):
        """Update the image in the UI thread."""
        if not HAS_TKINTER or self.root is None:
            return
            
        # Convert image for Tkinter
        self.tk_image = ImageTk.PhotoImage(self.image)
        
        # Update the image in the UI thread
        if self.panel is not None:
            self.root.after(0, self._update_image)
    
    def _update_image(self):
        """Update the image in the UI thread."""
        if self.panel is not None and self.tk_image is not None:
            self.panel.configure(image=self.tk_image)
            self.panel.image = self.tk_image 