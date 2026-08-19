on run argv
    if (count of argv) is not 3 then error "Ожидаются режим, исходный путь и итоговый путь."
    set operationMode to item 1 of argv
    set sourcePath to POSIX file (item 2 of argv)
    set destinationPath to POSIX file (item 3 of argv)

    tell application "Pages"
        if operationMode is "export" then
            set sourceDocument to open sourcePath
            try
                export sourceDocument to destinationPath as Microsoft Word
            on error errorMessage number errorNumber
                close sourceDocument saving no
                error errorMessage number errorNumber
            end try
            close sourceDocument saving no
        else if operationMode is "import" then
            set sourceDocument to open sourcePath
            try
                save sourceDocument in destinationPath
            on error errorMessage number errorNumber
                close sourceDocument saving no
                error errorMessage number errorNumber
            end try
            close sourceDocument saving no
        else
            error "Неизвестный режим Pages: " & operationMode
        end if
    end tell
end run
