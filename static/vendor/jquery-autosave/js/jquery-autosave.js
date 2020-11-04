;(function( $, window, document, undefined ){
  
var pluginName = 'autosave';
var defaults = {
  url:    "",
  method: "POST",
  event:  "change",
  data:   {},
  headers: {},
  type:   "html",
  debug:  false,
  before: function(){},
  done:   function(){},
  fail:   function(){},
  always: function(){}
};

function Plugin(element, options){
  this.element = element;
  //merge defaults and options without changing either
  this.options = $.extend( {}, defaults, options);
  
  this._defaults = defaults;
  this._name = pluginName;
  this.init();
}

Plugin.prototype.init = function(){
  var $this = $(this.element);
  var inline_options = getDataAttributes($this);
  var init_options = this.options; //preserve initial options
  
  //override just the event variable, check back and use updated data later
  var inline_options = getDataAttributes($this);
  init_options.event = inline_options.event || init_options.event;

  $this.on(init_options.event,function(e){

    // before, done, fail, always use init_options 
    // so as to not be overwritten by inline_options
    if(init_options.before){
      init_options.before.call($this); // call in the context of the element
    }

    var inline_options = getDataAttributes($this); //get latest inline options
    options = $.extend({}, init_options, inline_options); //inline options overwrite options
    var data = $.extend({}, options.data, inline_options); //include all inline options in data
    var headers = $.extend({}, options.headers, inline_options); //include all inline options in headers

    //if data.debug included as string, change to boolean
    if (options.debug == "false"){ options.debug = false; }

    //remove url, method, type, debug from request params, add event
    delete data.url; 
    delete data.method; 
    delete data.type; 
    delete data.debug;
    data.event = options.event;

    if(!options.debug) { // Unless in Debug Mode

      $.ajax({
        url:      options.url,
        type:     options.method,
        cache:    false,
        data:     data,
        headers:  headers,
        dataType: options.type
      })
      .done(function(data, textStatus, jqXHR){
        $this.data('autosave-data', data);
        $this.data('autosave-textStatus', textStatus);
        $this.data('autosave-jqXHR', jqXHR);
        $this.trigger('autosave-done');
      })
      .fail(function(jqXHR, textStatus, errorThrown){
        $this.data('autosave-jqXHR', jqXHR);
        $this.data('autosave-textStatus', textStatus);
        $this.data('autosave-errorThrown', errorThrown);
        $this.trigger('autosave-fail');
      })
      .always(function(){
        $this.trigger('autosave-always');
      });

    }else{
      console.log(data);
    }
  }); //end applying event handler

  // before, done, fail, always use init_options 
  // so as to not be overwritten by inline_options
  if (init_options.done){
    $this.on('autosave-done', function(){
      var data        = $this.data('autosave-data');
      var textStatus  = $this.data('autosave-textStatus');
      var jqXHR       = $this.data('autosave-jqXHR');
      options.done.call($this, data, textStatus, jqXHR);
    });
  }
  if (init_options.fail){
    $this.on('autosave-fail', function(){ 
      var jqXHR       = $this.data('autosave-jqXHR');
      var textStatus  = $this.data('autosave-textStatus');
      var errorThrown = $this.data('autosave-errorThrown');
      options.fail.call($this, jqXHR, textStatus, errorThrown);
    });
  }
  if (init_options.always){
    $this.on('autosave-always', function(){
      options.always.call($this);
    });
  }

  function getDataAttributes($element){
    var data_attribute_regex = /^data\-(\w+)$/;
    var data_attributes = {};
    data_attributes.value = $element.val() || "";
    data_attributes.name  = $element.attr('name') || "";
    

    $($element[0].attributes).each(function(){
      if(data_attribute_regex.test(this.name)){
        attribute_name = data_attribute_regex.exec(this.name)[1]
        data_attributes[attribute_name] = this.value;
      }
    });
    
    return data_attributes;
  }// end getDataAttributes()

}//end init()

$.fn.autosave = function ( options ) {
  return this.each(function () {
    new Plugin( this, options );
  });
};//end plugin
  
})( jQuery, window, document );
