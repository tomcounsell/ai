// Global HTMX configuration
htmx.config.withCredentials = true;  // Enable cross-site requests
htmx.config.globalViewTransitions = true;  // Enable view transitions by default
htmx.config.defaultSwapStyle = "outerHTML";  // Set outerHTML as the default swap style
htmx.config.requestClass = "loading";  // Apply 'loading' class to elements during requests

// Global trigger function for dispatching events via DOM, jQuery, HTMX
function trigger(target, eventName, detail = null) {
  // Handle string selectors or direct element references
  const targetElement = typeof target === 'string'
    ? document.querySelector(target)
    : target;

  if (!targetElement) {
    console.warn(`Target element not found for event: ${eventName}`);
    return;
  }

  // Create event object
  const event = detail
    ? new CustomEvent(eventName, {detail, bubbles: true})
    : new Event(eventName, {bubbles: true});

  // DOM native event - dispatch on the target element
  targetElement.dispatchEvent(event);

  // jQuery event
  if (window.jQuery) {
    $(targetElement).trigger(eventName, detail);
  }

  // HTMX event
  if (window.htmx) {
    htmx.trigger(targetElement, eventName, detail);
  }
}

// Toggle dropdown menu visibility
function toggleDropdown(button) {
  const menuId = button.getAttribute('aria-controls');
  const menu = document.getElementById(menuId);

  if (!menu) return;

  const isExpanded = button.getAttribute('aria-expanded') === 'true';
  button.setAttribute('aria-expanded', !isExpanded);
  menu.classList.toggle('hidden', isExpanded);

  // Toggle chevron icon rotation if present
  const chevron = button.querySelector('.fa-chevron-down');
  if (chevron) {
    chevron.classList.toggle('transform', !isExpanded);
    chevron.classList.toggle('rotate-180', !isExpanded);
  }

  // Close dropdown when clicking outside
  if (!isExpanded) {
    document.addEventListener('click', function closeDropdown(e) {
      if (!menu.contains(e.target) && !button.contains(e.target)) {
        button.setAttribute('aria-expanded', 'false');
        menu.classList.add('hidden');
        // Reset chevron icon
        if (chevron) {
          chevron.classList.remove('transform', 'rotate-180');
        }
        document.removeEventListener('click', closeDropdown);
      }
    });
  }
}

// Toggle mobile menu visibility
function toggleMobileMenu() {
  const menu = document.getElementById('mobile-menu');
  const button = document.getElementById('mobile-menu-button');

  if (!menu || !button) return;

  const isExpanded = button.getAttribute('aria-expanded') === 'true';
  button.setAttribute('aria-expanded', !isExpanded);
  menu.classList.toggle('hidden', isExpanded);
}

// Add CSRF token to every request
document.body.addEventListener("htmx:configRequest", function(configEvent){
    configEvent.detail.headers['X-CSRFToken'] = document.querySelector('html').getAttribute('data-csrf-token');
});

// Custom extension for removing elements with animation
htmx.defineExtension('remove-with-animation', {
    onEvent: function(name, evt) {
        if (name === 'htmx:beforeRemove') {
            const elt = evt.detail.target;
            evt.detail.removeHandler = function() {
                elt.style.opacity = '0';
                elt.style.maxHeight = '0';
                elt.style.overflow = 'hidden';

                // After animation completes, actually remove the element
                setTimeout(function() {
                    elt.remove();
                }, 500); // Match the duration in CSS
            };
        }
    }
});

// Ensure logout form submits properly
document.addEventListener('DOMContentLoaded', function() {
    // Find all logout buttons and add event listeners
    const logoutForms = document.querySelectorAll('.logout-form');

    logoutForms.forEach(form => {
        form.addEventListener('submit', function(event) {
            // Prevent the default form behavior
            event.preventDefault();

            // Create FormData object
            const formData = new FormData(form);

            // Use fetch to submit the form via POST
            fetch(form.action, {
                method: 'POST',
                body: formData,
                headers: {
                    'X-CSRFToken': document.querySelector('html').getAttribute('data-csrf-token')
                },
                credentials: 'same-origin'
            }).then(response => {
                if (response.redirected) {
                    // If the response is a redirect, follow it
                    window.location.href = response.url;
                } else {
                    // Reload the page if not redirected
                    window.location.reload();
                }
            }).catch(error => {
                console.error('Logout error:', error);
                // Reload the page anyway
                window.location.reload();
            });
        });
    });
});
