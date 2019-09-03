const transcribed_answer = answer
$("#answer").remove()
$("#send-message").click(function() {
  const subject = $('.contact-form input[name="subject"]').val()
  const email = $('.contact-form input[name="email"]').val()
  const message = $('.contact-form textarea[name="message"]').val()
  const captcha = $('.contact-form input[name="captcha"]').val()

  if (validateSubject(subject) && validateEmail(email) && validateMessage(message) && validateCaptcha(captcha)) {
    $(this).addClass("is-loading")
    $.ajax({
      type: 'POST',
      url: 'send_message',
      contentType: "application/json; charset=UTF-8",
      dataType: "json",
      data: JSON.stringify({
        subject: subject,
        email: email,
        message: message
      }),
      success: function(data) {
        if (typeof data == 'string')
          data = JSON.parse(data)
        if (data.status == "200") {
          showMessage("Message successfully sent!", true)
        } else if (data.status == "403") {
          showMessage("Invalid form inputs; please try again", false)
        } else {
          showMessage("Error: Could not send message", false)
        }
      },
      error: function(err) {
        showMessage("Error: Could not send message", false)
      }
    })
  } else {
    $("input").blur()
    $("textarea").blur()
  }
})

function showMessage(message, success) {
  alert(message)
  if (success) {
    window.location.reload()
  } else {
    $("#send-message").removeClass("is-loading")
  }
}

function validateEmail(email) {
  const re = /^(([^<>()\[\]\\.,;:\s@"]+(\.[^<>()\[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,}))$/;
  return re.test(String(email).toLowerCase());
}

function validateSubject(subject) {
  return subject.length > 0
}

function validateMessage(message) {
  return message.length > 0
}

function validateCaptcha(captcha) {
  return parseInt(captcha) === transcribed_answer
}

// Errors defined in login-form.html
function showError(input) {
  input.addClass("is-danger")
  input.parents(".field").find(".help").removeClass("hidden")
}

function hideError(input) {
  input.removeClass("is-danger")
  input.parents(".field").find(".help").addClass("hidden")
}

// Validation
$('.contact-form input, .contact-form textarea').on("blur", function() {
  const field = $(this).attr("name")
  console.log(field)
  const value = $(this).val()
  var valid = false;

  switch (field) {
    case "subject":
      valid = validateSubject(value)
      break
    case "email":
      valid = validateEmail(value)
      break
    case "message":
      valid = validateMessage(value)
      break
    case "captcha":
      valid = validateCaptcha(value)
      break
  }

  // invalid password
  if (!valid) {
    showError($(this))
  } else {
    hideError($(this))
  }
})

$('.contact-form input, .contact-form textarea').on("change paste keyup", function() {
  const field = $(this).attr("name")
  console.log(field)
  const value = $(this).val()
  var valid = false;

  switch (field) {
    case "subject":
      valid = validateSubject(value)
      break
    case "email":
      valid = validateEmail(value)
      break
    case "message":
      valid = validateMessage(value)
      break
    case "captcha":
      valid = validateCaptcha(value)
      break
  }

  if (valid) {
    hideError($(this))
  }
})

// form submission on enter press
$('input').keyup(function(e){
    if (e.keyCode == 13) { // enter key pressed
        // const isLogin = $(this).parents('.modal').is('#login-modal')
        // const mode = isLogin ? "login" : "signup"
        // doAuth(mode)
    }
});
