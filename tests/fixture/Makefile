FILES := \
	 subdir/bar.cpp \
	 subdir/foo.c \
	 toplev.c \

OBJFILES := $(patsubst %,%.o,${FILES})

%.c.o: %.c
	${CC} ${CFLAGS} -c -o $@ $^

%.cpp.o: %.cpp
	${CXX} ${CFLAGS} -c -o $@ $^

compile_commands.json: ${FILES}
	bear -- ${MAKE} clean default

default: ${OBJFILES}

clean:
	rm -f ${OBJFILES}

.PHONY: default clean
