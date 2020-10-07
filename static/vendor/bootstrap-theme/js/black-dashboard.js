"use strict";

/*!

=========================================================
* Black Dashboard - v1.1.0
=========================================================

* Product Page: https://themes.getbootstrap.com/product/black-dashboard-pro-premium-bootstrap-4-admin/
* Copyright 2019 Creative Tim (https://www.creative-tim.com)
* Coded by Creative Tim

=========================================================

* The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

*/

var transparent = true;
var transparentDemo = true;
var fixedTop = false;

var navbar_initialized = false;
var backgroundOrange = false;
var sidebar_mini_active = false;
var toggle_initialized = false;

var $html = $('html');
var $body = $('body');
var $navbar_minimize_fixed = $('.navbar-minimize-fixed');
var $collapse = $('.collapse');
var $navbar = $('.navbar');
var $tagsinput = $('.tagsinput');
var $selectpicker = $('.selectpicker');
var $navbar_color = $('.navbar[color-on-scroll]');
var $full_screen_map = $('.full-screen-map');
var $datetimepicker = $('.datetimepicker');
var $datepicker = $('.datepicker');
var $timepicker = $('.timepicker');

var seq = 0,
  delays = 80,
  durations = 500;
var seq2 = 0,
  delays2 = 80,
  durations2 = 500;

// Returns a function, that, as long as it continues to be invoked, will not
// be triggered. The function will be called after it stops being called for
// N milliseconds. If `immediate` is passed, trigger the function on the
// leading edge, instead of the trailing.

function debounce(func, wait, immediate) {
  var timeout;
  return function() {
    var context = this,
      args = arguments;
    clearTimeout(timeout);
    timeout = setTimeout(function() {
      timeout = null;
      if (!immediate) func.apply(context, args);
    }, wait);
    if (immediate && !timeout) func.apply(context, args);
  };
};

(function() {
  var isWindows = navigator.platform.indexOf('Win') > -1 ? true : false;

  if (isWindows) {
    // if we are on windows OS we activate the perfectScrollbar function
    if ($('.main-panel').length != 0) {
      var ps = new PerfectScrollbar('.main-panel', {
        wheelSpeed: 2,
        wheelPropagation: true,
        minScrollbarLength: 20,
        suppressScrollX: true
      });
    }

    if ($('.sidebar .sidebar-wrapper').length != 0) {

      var ps1 = new PerfectScrollbar('.sidebar .sidebar-wrapper');
      $('.table-responsive').each(function() {
        var ps2 = new PerfectScrollbar($(this)[0]);
      });
    }



    $html.addClass('perfect-scrollbar-on');
  } else {
    $html.addClass('perfect-scrollbar-off');
  }
})();

$(document).ready(function() {

  var scroll_start = 0;
  var startchange = $('.row');
  var offset = startchange.offset();
  var scrollElement = navigator.platform.indexOf('Win') > -1 ? $(".ps") : $(window);
  scrollElement.scroll(function() {

    scroll_start = $(this).scrollTop();

    if (scroll_start > 50) {
      $navbar_minimize_fixed.css('opacity', '1');
    } else {
      $navbar_minimize_fixed.css('opacity', '0');
    }
  });

  // hide the siblings opened collapse

  $collapse.on('show.bs.collapse', function() {
    $(this).parent().siblings().children('.collapse').each(function() {
      $(this).collapse('hide');
    });
  });

  //  Activate the Tooltips
  $('[data-toggle="tooltip"], [rel="tooltip"]').tooltip();

  // Activate Popovers and set color for popovers
  $('[data-toggle="popover"]').each(function() {
    color_class = $(this).data('color');
    $(this).popover({
      template: '<div class="popover popover-' + color_class + '" role="tooltip"><div class="arrow"></div><h3 class="popover-header"></h3><div class="popover-body"></div></div>'
    });
  });

  var tagClass = $tagsinput.data('color');

  if ($tagsinput.length != 0) {
    $tagsinput.tagsinput();
  }

  $('.bootstrap-tagsinput').find('.tag').addClass('badge-' + tagClass);

  //    Activate bootstrap-select
  if ($selectpicker.length != 0) {
    $selectpicker.selectpicker({
      iconBase: "tim-icons",
      tickIcon: "icon-check-2"
    });
  }

  //when you click the modal search button the navbar will not be collapsed
  $("#search-button").click(function() {
    $(this).closest('.navbar-collapse').removeClass('show');
    $navbar.addClass('navbar-transparent').removeClass('bg-white');

  });



  blackDashboard.initMinimizeSidebar();

  var scroll_distance = $navbar_color.attr('color-on-scroll') || 500;

  // Check if we have the class "navbar-color-on-scroll" then add the function to remove the class "navbar-transparent" so it will transform to a plain color.
  if ($navbar_color.length != 0) {
    blackDashboard.checkScrollForTransparentNavbar();
    $(window).on('scroll', blackDashboard.checkScrollForTransparentNavbar)
  }

  if ($full_screen_map.length == 0 && $('.bd-docs').length == 0) {
    // On click navbar-collapse the menu will be white not transparent
    $('.navbar-toggler').click(function() {
      $collapse.on('show.bs.collapse', function() {
        $(this).closest('.navbar').removeClass('navbar-transparent').addClass('bg-white');
      }).on('hide.bs.collapse', function() {
        $(this).closest('.navbar').addClass('navbar-transparent').removeClass('bg-white');
      });
      $navbar.css('transition', '');

    });
  }

  $navbar.css({
    'top': '0',
    'transition': 'all .5s linear'
  });

  $('.form-control').on("focus", function() {
    $(this).parent('.input-group').addClass("input-group-focus");
  }).on("blur", function() {
    $(this).parent(".input-group").removeClass("input-group-focus");
  });

  // Activate bootstrapSwitch
  $('.bootstrap-switch').each(function() {
    var data_on_label = $(this).data('on-label') || '';
    var data_off_label = $(this).data('off-label') || '';

    $(this).bootstrapSwitch({
      onText: data_on_label,
      offText: data_off_label
    });
  });
});

$(document).on('click', '.navbar-toggle', function() {
  var $toggle = $(this);

  if (blackDashboard.misc.navbar_menu_visible == 1) {
    $html.removeClass('nav-open');
    blackDashboard.misc.navbar_menu_visible = 0;
    setTimeout(function() {
      $toggle.removeClass('toggled');
      $('.bodyClick').remove();
    }, 550);

  } else {
    setTimeout(function() {
      $toggle.addClass('toggled');
    }, 580);

    var div = '<div class="bodyClick"></div>';
    $(div).appendTo('body').click(function() {
      $html.removeClass('nav-open');
      blackDashboard.misc.navbar_menu_visible = 0;
      setTimeout(function() {
        $toggle.removeClass('toggled');
        $('.bodyClick').remove();
      }, 550);
    });

    $html.addClass('nav-open');
    blackDashboard.misc.navbar_menu_visible = 1;
  }
});

$(window).resize(function() {
  // reset the seq for charts drawing animations
  seq = seq2 = 0;

  if ($full_screen_map.length == 0 && $('.bd-docs').length == 0) {
    var isExpanded = $navbar.find('[data-toggle="collapse"]').attr("aria-expanded");
    if ($navbar.hasClass('bg-white') && $(window).width() > 991) {
      $navbar.removeClass('bg-white').addClass('navbar-transparent');
    } else if ($navbar.hasClass('navbar-transparent') && $(window).width() < 991 && isExpanded != "false") {
      $navbar.addClass('bg-white').removeClass('navbar-transparent');
    }
  }
});

var blackDashboard = {
  misc: {
    navbar_menu_visible: 0
  },

  checkScrollForTransparentNavbar: debounce(function() {
    if ($(document).scrollTop() > scroll_distance) {
      if (transparent) {
        transparent = false;
        $navbar_color.removeClass('navbar-transparent');
      }
    } else {
      if (!transparent) {
        transparent = true;
        $navbar_color.addClass('navbar-transparent');
      }
    }
  }, 17),



  // Activate DateTimePicker

  initDateTimePicker: function() {
    if ($datetimepicker.length != 0) {
      $datetimepicker.datetimepicker({
        icons: {
          time: "tim-icons icon-watch-time",
          date: "tim-icons icon-calendar-60",
          up: "fa fa-chevron-up",
          down: "fa fa-chevron-down",
          previous: 'tim-icons icon-minimal-left',
          next: 'tim-icons icon-minimal-right',
          today: 'fa fa-screenshot',
          clear: 'fa fa-trash',
          close: 'fa fa-remove'
        }
      });
    }

    if ($datepicker.length != 0) {
      $datepicker.datetimepicker({
        format: 'MM/DD/YYYY',
        icons: {
          time: "tim-icons icon-watch-time",
          date: "tim-icons icon-calendar-60",
          up: "fa fa-chevron-up",
          down: "fa fa-chevron-down",
          previous: 'tim-icons icon-minimal-left',
          next: 'tim-icons icon-minimal-right',
          today: 'fa fa-screenshot',
          clear: 'fa fa-trash',
          close: 'fa fa-remove'
        }
      });
    }

    if ($timepicker.length != 0) {
      $timepicker.datetimepicker({
        // format: 'H:mm',    // use this format if you want the 24hours timepicker
        format: 'h:mm A', //use this format if you want the 12hours timpiecker with AM/PM toggle
        icons: {
          time: "tim-icons icon-watch-time",
          date: "tim-icons icon-calendar-60",
          up: "fa fa-chevron-up",
          down: "fa fa-chevron-down",
          previous: 'tim-icons icon-minimal-left',
          next: 'tim-icons icon-minimal-right',
          today: 'fa fa-screenshot',
          clear: 'fa fa-trash',
          close: 'fa fa-remove'
        }
      });
    }
  },

  initMinimizeSidebar: function() {
    if ($('.sidebar-mini').length != 0) {
      sidebar_mini_active = true;
    }

    $('.minimize-sidebar').click(function() {

      if (sidebar_mini_active == true) {
        $body.removeClass('sidebar-mini');
        sidebar_mini_active = false;
        // blackDashboard.showSidebarMessage('Sidebar mini deactivated...');
      } else {
        $body.addClass('sidebar-mini');
        sidebar_mini_active = true;
        // blackDashboard.showSidebarMessage('Sidebar mini activated...');
      }

      // we simulate the window Resize so the charts will get updated in realtime.
      var simulateWindowResize = setInterval(function() {
        window.dispatchEvent(new Event('resize'));
      }, 180);

      // we stop the simulation of Window Resize after the animations are completed
      setTimeout(function() {
        clearInterval(simulateWindowResize);
      }, 1000);
    });
  },

  startAnimationForLineChart: function(chart) {
    chart.on('draw', function(data) {
      if (data.type === 'line' || data.type === 'area') {
        data.element.animate({
          d: {
            begin: 600,
            dur: 700,
            from: data.path.clone().scale(1, 0).translate(0, data.chartRect.height()).stringify(),
            to: data.path.clone().stringify(),
            easing: Chartist.Svg.Easing.easeOutQuint
          }
        });
      } else if (data.type === 'point') {
        seq++;
        data.element.animate({
          opacity: {
            begin: seq * delays,
            dur: durations,
            from: 0,
            to: 1,
            easing: 'ease'
          }
        });
      }
    });

    seq = 0;
  },
  startAnimationForBarChart: function(chart) {

    chart.on('draw', function(data) {
      if (data.type === 'bar') {
        seq2++;
        data.element.animate({
          opacity: {
            begin: seq2 * delays2,
            dur: durations2,
            from: 0,
            to: 1,
            easing: 'ease'
          }
        });
      }
    });

    seq2 = 0;
  },
  showSidebarMessage: function(message) {
    try {
      $.notify({
        icon: "tim-icons icon-bell-55",
        message: message
      }, {
        type: 'primary',
        timer: 4000,
        placement: {
          from: 'top',
          align: 'right'
        }
      });
    } catch (e) {
      console.log('Notify library is missing, please make sure you have the notifications library added.');
    }

  }
};

function hexToRGB(hex, alpha) {
  var r = parseInt(hex.slice(1, 3), 16),
    g = parseInt(hex.slice(3, 5), 16),
    b = parseInt(hex.slice(5, 7), 16);

  if (alpha) {
    return "rgba(" + r + ", " + g + ", " + b + ", " + alpha + ")";
  } else {
    return "rgb(" + r + ", " + g + ", " + b + ")";
  }
}
